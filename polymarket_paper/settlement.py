"""结算:优先采用 Polymarket 官方结算结果,未出结果时用 Binance 区间
开/收盘价近似(两者价格源不同,存在极小概率的边缘分歧,报表以
resolution_source 字段区分)。
"""

from __future__ import annotations

import json
import logging
import time

from .config import Config
from .feeds.binance import fetch_interval_kline
from .feeds.polymarket import PolymarketClient
from .storage import Store
from .trader import trade_pnl

log = logging.getLogger(__name__)


def outcome_from_prices(open_price: float, close_price: float,
                        tie_resolves_down: bool = False) -> int:
    """1=Up, 0=Down。官方规则:结束价 >= 开始价判 Up,平局默认算 Up。"""
    if close_price > open_price:
        return 1
    if close_price < open_price:
        return 0
    return 0 if tie_resolves_down else 1


def outcome_from_gamma(market: dict) -> int | None:
    """从 Gamma 市场对象解析官方结算结果;未结算返回 None。

    已结算市场的 outcomePrices 形如 '["1", "0"]',顺序与 outcomes 对应。
    """
    if not market.get("closed"):
        return None
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not outcomes or not prices:
        return None
    try:
        winner = {o.strip().lower(): float(p) for o, p in zip(outcomes, prices)}
    except (ValueError, AttributeError):
        return None
    if winner.get("up") == 1.0:
        return 1
    if winner.get("down") == 1.0:
        return 0
    return None


async def _fetch_gamma_outcome(client: PolymarketClient,
                               condition_id: str) -> int | None:
    try:
        data = await client._get_json(
            f"{client.cfg.gamma_url}/markets",
            params={"condition_ids": condition_id})
        for m in data if isinstance(data, list) else []:
            outcome = outcome_from_gamma(m)
            if outcome is not None:
                return outcome
    except Exception as exc:
        log.debug("Gamma 结算查询失败: %s", exc)
    return None


async def settle_pending(store: Store, cfg: Config,
                         client: PolymarketClient) -> float:
    """结算所有已到期市场,更新虚拟资金,返回本轮净损益合计。"""
    total = 0.0
    for market in store.unsettled_markets(before_ts=time.time()):
        market_id = market["market_id"]
        outcome: int | None = None
        close_price: float | None = None
        source = "polymarket"

        outcome = await _fetch_gamma_outcome(client, market_id)
        if outcome is None:
            # Binance 近似:用记录的区间开盘参照价与 15m K 线收盘价
            source = "binance"
            kline = await fetch_interval_kline(
                cfg.binance, market["interval_start"],
                cfg.polymarket.interval_minutes)
            if kline is None:
                log.info("市场 %s 的 K 线尚未收盘,稍后重试", market_id)
                continue
            kline_open, close_price = kline
            open_price = market["open_price"] if market["open_price"] is not None else kline_open
            outcome = outcome_from_prices(
                open_price, close_price, cfg.strategy.tie_resolves_down)

        pnl = store.settle_market(
            market_id, close_price=close_price, outcome_up=outcome,
            source=source, pnl_fn=trade_pnl)
        total += pnl
        log.info("市场 %s 结算: %s(来源 %s),净损益 %+.2f USDC",
                 market_id[:16], "Up" if outcome else "Down", source, pnl)

    if total != 0.0:
        bankroll = store.get_bankroll(cfg.bankroll.initial_usdc) + total
        store.set_bankroll(bankroll)
        log.info("虚拟资金更新: %.2f USDC", bankroll)
    return total
