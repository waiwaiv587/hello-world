"""Polymarket 数据层:Gamma 市场发现 + CLOB 订单簿(WS 订阅,REST 兜底)。

纸面模式只读公开数据,不需要任何密钥;网络层用本项目的标准库实现
(miniws / netutil),零第三方依赖。筛选逻辑抽成纯函数,便于离线单测。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from .. import miniws, netutil
from ..config import PolymarketCfg

log = logging.getLogger(__name__)


@dataclass
class MarketInfo:
    condition_id: str
    slug: str
    question: str
    token_id_up: str
    token_id_down: str
    interval_start: float   # epoch 秒
    interval_end: float
    fee_bps: float = 0.0


def _parse_iso(ts: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def select_current_market(
    markets: list[dict],
    now_s: float,
    title_regex: str,
    interval_minutes: int = 15,
) -> MarketInfo | None:
    """从 Gamma /markets 返回里挑出"当前 15 分钟区间"的 BTC up/down 市场。

    判定:标题匹配正则,且 endDate 落在 15 分钟整点边界、距 now 不超过
    一个区间长度。interval_start = endDate − 区间长度(不依赖 Gamma 的
    startDate 字段语义)。outcomes 里 Up/Down 的顺序决定 token 映射。
    """
    pattern = re.compile(title_regex, re.IGNORECASE)
    interval_s = interval_minutes * 60
    best: MarketInfo | None = None
    for m in markets:
        question = m.get("question") or m.get("title") or ""
        if not pattern.search(question):
            continue
        end_raw = m.get("endDate") or m.get("end_date_iso")
        if not end_raw:
            continue
        try:
            end_s = _parse_iso(end_raw)
        except ValueError:
            continue
        if end_s <= now_s or end_s > now_s + interval_s:
            continue
        if int(end_s) % interval_s != 0:
            continue
        try:
            outcomes = m["outcomes"]
            token_ids = m["clobTokenIds"]
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            mapping = {o.strip().lower(): t for o, t in zip(outcomes, token_ids)}
            token_up, token_down = mapping["up"], mapping["down"]
        except (KeyError, ValueError, TypeError):
            continue
        info = MarketInfo(
            condition_id=m.get("conditionId") or m.get("condition_id") or "",
            slug=m.get("slug", ""),
            question=question,
            token_id_up=token_up,
            token_id_down=token_down,
            interval_start=end_s - interval_s,
            interval_end=end_s,
        )
        if not info.condition_id:
            continue
        # 多个候选取最早到期的(即当前区间)
        if best is None or info.interval_end < best.interval_end:
            best = info
    return best


class PolymarketClient:
    """Gamma / CLOB 的只读 REST 封装。"""

    def __init__(self, cfg: PolymarketCfg):
        self.cfg = cfg

    async def _get_json(self, url: str, params: dict | None = None):
        return await netutil.get_json(url, params=params)

    async def discover_current_market(self) -> MarketInfo | None:
        """自动发现当前 15 分钟 BTC up/down 市场;支持配置手动钉死。"""
        cfg = self.cfg
        if cfg.override_condition_id:
            now = time.time()
            interval_s = cfg.interval_minutes * 60
            end = (int(now) // interval_s + 1) * interval_s
            return MarketInfo(
                condition_id=cfg.override_condition_id, slug="(override)",
                question="(override)", token_id_up=cfg.override_token_id_up,
                token_id_down=cfg.override_token_id_down,
                interval_start=end - interval_s, interval_end=end)
        markets = await self._get_json(
            f"{cfg.gamma_url}/markets",
            params={"closed": "false", "order": "endDate", "ascending": "true",
                    "limit": 300})
        info = select_current_market(
            markets, time.time(), cfg.series_title_regex, cfg.interval_minutes)
        if info is not None:
            info.fee_bps = await self.fetch_taker_fee_bps(info.condition_id)
        return info

    async def fetch_taker_fee_bps(self, condition_id: str) -> float:
        """从 CLOB 市场对象读取真实 taker 费率(基点);失败返回 -1 由调用方兜底。"""
        try:
            data = await self._get_json(
                f"{self.cfg.clob_rest_url}/markets/{condition_id}")
            return float(data.get("taker_base_fee", -1))
        except Exception as exc:
            log.warning("读取 taker 费率失败(%s),使用配置兜底值", exc)
            return -1.0

    async def fetch_book_top(self, token_id: str) -> tuple[float | None, float | None]:
        """REST 取某 token 的 (best_bid, best_ask)。"""
        data = await self._get_json(
            f"{self.cfg.clob_rest_url}/book", params={"token_id": token_id})
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        best_bid = max((float(b["price"]) for b in bids), default=None)
        best_ask = min((float(a["price"]) for a in asks), default=None)
        return best_bid, best_ask


def apply_book_message(
    books: dict[str, dict[str, float | None]], msg: dict
) -> None:
    """把 CLOB market 频道的一条消息合并进 books 状态。

    books: token_id -> {"bid": float|None, "ask": float|None}
    支持 'book'(全量快照)与 'price_change'(增量,只在触及当前
    最优价时保守地失效/更新)两类事件。
    """
    event = msg.get("event_type")
    token = msg.get("asset_id")
    if not token:
        return
    slot = books.setdefault(token, {"bid": None, "ask": None})
    if event == "book":
        bids = msg.get("bids") or msg.get("buys") or []
        asks = msg.get("asks") or msg.get("sells") or []
        slot["bid"] = max((float(b["price"]) for b in bids), default=None)
        slot["ask"] = min((float(a["price"]) for a in asks), default=None)
    elif event == "price_change":
        for ch in msg.get("changes", []):
            price = float(ch["price"])
            size = float(ch["size"])
            side = ch.get("side", "").upper()
            if side == "BUY":
                if size > 0 and (slot["bid"] is None or price > slot["bid"]):
                    slot["bid"] = price
                elif size == 0 and slot["bid"] is not None and price >= slot["bid"]:
                    slot["bid"] = None  # 最优价被撤,等下一次快照/轮询修复
            elif side == "SELL":
                if size > 0 and (slot["ask"] is None or price < slot["ask"]):
                    slot["ask"] = price
                elif size == 0 and slot["ask"] is not None and price <= slot["ask"]:
                    slot["ask"] = None


@dataclass
class BookFeed:
    """订单簿状态:WS 订阅为主,REST 轮询兜底。"""

    cfg: PolymarketCfg
    client: PolymarketClient
    token_ids: list[str]
    books: dict[str, dict[str, float | None]] = field(default_factory=dict)

    def top(self, token_id: str) -> tuple[float | None, float | None]:
        slot = self.books.get(token_id) or {}
        return slot.get("bid"), slot.get("ask")

    async def run(self) -> None:
        ws_task = asyncio.ensure_future(self._run_ws())
        poll_task = asyncio.ensure_future(self._run_poll())
        try:
            await asyncio.gather(ws_task, poll_task)
        finally:
            ws_task.cancel()
            poll_task.cancel()

    async def _run_ws(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with miniws.connect(self.cfg.clob_ws_url,
                                          ping_interval_s=10.0) as ws:
                    await ws.send(json.dumps(
                        {"type": "market", "assets_ids": self.token_ids}))
                    log.info("Polymarket 订单簿 WS 已订阅 %d 个 token", len(self.token_ids))
                    backoff = 1.0
                    async for raw in ws:
                        payload = json.loads(raw)
                        msgs = payload if isinstance(payload, list) else [payload]
                        for msg in msgs:
                            apply_book_message(self.books, msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Polymarket WS 断开(%s),%.0fs 后重连", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _run_poll(self) -> None:
        """REST 轮询兜底:WS 增量撤单后最优价缺失时由这里修复。"""
        while True:
            await asyncio.sleep(self.cfg.book_poll_fallback_s)
            for token in self.token_ids:
                slot = self.books.setdefault(token, {"bid": None, "ask": None})
                if slot["bid"] is not None and slot["ask"] is not None:
                    continue
                try:
                    bid, ask = await self.client.fetch_book_top(token)
                    slot["bid"], slot["ask"] = bid, ask
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.debug("REST 订单簿轮询失败: %s", exc)
