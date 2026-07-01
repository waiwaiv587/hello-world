"""Binance BTC 现货参照流:WebSocket 成交流 + REST K 线。

网络库(websockets / httpx)在函数内懒加载,纯逻辑部分可离线单测。
"""

from __future__ import annotations

import asyncio
import json
import logging

from ..config import BinanceCfg
from ..signal import EwmaVol

log = logging.getLogger(__name__)


def parse_trade(msg: str) -> tuple[float, float] | None:
    """解析 @trade 消息,返回 (price, epoch秒);非成交消息返回 None。"""
    data = json.loads(msg)
    if data.get("e") != "trade":
        return None
    return float(data["p"]), float(data["T"]) / 1000.0


class BinanceFeed:
    """维护最新成交价与 EWMA 已实现波动率;断线自动重连。"""

    def __init__(self, cfg: BinanceCfg, vol: EwmaVol):
        self.cfg = cfg
        self.vol = vol
        self.last_price: float | None = None
        self.last_ts: float | None = None

    async def run(self) -> None:
        import websockets  # 懒加载:仅实盘数据采集需要

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.cfg.ws_url) as ws:
                    log.info("Binance WS 已连接: %s", self.cfg.ws_url)
                    backoff = 1.0
                    async for msg in ws:
                        parsed = parse_trade(msg)
                        if parsed is None:
                            continue
                        price, ts = parsed
                        self.last_price, self.last_ts = price, ts
                        self.vol.update(price, ts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Binance WS 断开(%s),%.0fs 后重连", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


async def fetch_interval_kline(
    cfg: BinanceCfg, interval_start_s: float, interval_minutes: int = 15
) -> tuple[float, float] | None:
    """取指定 15 分钟区间的 (open, close)。区间未走完或无数据返回 None。"""
    import httpx  # 懒加载

    params = {
        "symbol": cfg.symbol,
        "interval": f"{interval_minutes}m",
        "startTime": int(interval_start_s * 1000),
        "limit": 1,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{cfg.rest_url}/api/v3/klines", params=params)
        resp.raise_for_status()
        rows = resp.json()
    if not rows:
        return None
    k = rows[0]
    # K 线未收盘时 closeTime(k[6]) 在未来,不能用作结算
    import time
    if float(k[6]) / 1000.0 > time.time():
        return None
    return float(k[1]), float(k[4])
