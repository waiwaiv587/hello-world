"""模拟入场决策(纯函数,无副作用,无任何真实下单路径)。

规则:对 Up/Down 两侧分别算 edge = own_prob(该侧) − 该侧 taker 买价(ask),
取 edge 较大的一侧;仅当 edge > edge_threshold(默认 5 个百分点)才记一笔
假想单。用 ask 而非 mid 作为入场价,点差成本天然计入。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .sizing import size_position


@dataclass
class TradeIntent:
    side: str          # "UP" / "DOWN"
    prob: float        # 该侧的自估中奖概率
    price: float       # 入场价(该侧 best ask)
    edge: float        # prob − price
    stake: float       # 本金(USDC)
    shares: float
    fee: float         # taker 费(USDC)


def decide(
    own_prob_up: float,
    ask_up: float | None,
    ask_down: float | None,
    bankroll: float,
    cfg: Config,
    fee_bps: float,
) -> TradeIntent | None:
    """给定信号与订单簿卖一价,返回假想单意图;不满足条件返回 None。"""
    candidates: list[tuple[str, float, float]] = []
    if ask_up is not None and 0.0 < ask_up < 1.0:
        candidates.append(("UP", own_prob_up, ask_up))
    if ask_down is not None and 0.0 < ask_down < 1.0:
        candidates.append(("DOWN", 1.0 - own_prob_up, ask_down))
    if not candidates:
        return None

    side, prob, price = max(candidates, key=lambda c: c[1] - c[2])
    edge = prob - price
    # 严格大于阈值才开仓;1e-9 容差吸收浮点误差
    if edge <= cfg.strategy.edge_threshold + 1e-9:
        return None

    stake, shares, fee = size_position(
        q=prob,
        price=price,
        bankroll=bankroll,
        kelly_multiplier=cfg.bankroll.kelly_multiplier,
        max_stake_fraction=cfg.bankroll.max_stake_fraction,
        fee_bps=fee_bps,
    )
    if stake < cfg.strategy.min_stake_usdc:
        return None
    return TradeIntent(side=side, prob=prob, price=price, edge=edge,
                       stake=stake, shares=shares, fee=fee)


def trade_pnl(side: str, entry_price: float, shares: float, fee: float,
              outcome_up: int) -> float:
    """结算一笔假想单的净损益(USDC),含入场 taker 费。"""
    won = (side == "UP" and outcome_up == 1) or (side == "DOWN" and outcome_up == 0)
    if won:
        return shares * (1.0 - entry_price) - fee
    return -shares * entry_price - fee
