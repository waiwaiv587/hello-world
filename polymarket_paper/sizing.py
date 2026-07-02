"""仓位与费用:分数 Kelly + 单笔上限 + Polymarket taker 费模型。

二元合约(赢付 1)在价格 p 买入、自估中奖概率 q 时的完整 Kelly 比例:
    f* = (q − p) / (1 − p)
实际下注比例 = min(f* × kelly_multiplier, max_stake_fraction),
且 本金 + 手续费 合计不超过 bankroll × max_stake_fraction
(单笔最大亏损即本金+费,故该上限等价于单笔 CVaR ≤ 2%)。

taker 费(Polymarket 短周期加密市场公式):
    fee = bps/10000 × min(p, 1−p) × shares
"""

from __future__ import annotations


def kelly_fraction(q: float, price: float) -> float:
    """完整 Kelly 比例;无正期望或价格非法时返回 0。"""
    if not (0.0 < price < 1.0):
        return 0.0
    return max(0.0, (q - price) / (1.0 - price))


def taker_fee(price: float, shares: float, fee_bps: float) -> float:
    return (fee_bps / 1e4) * min(price, 1.0 - price) * shares


def size_position(
    q: float,
    price: float,
    bankroll: float,
    kelly_multiplier: float = 0.25,
    max_stake_fraction: float = 0.02,
    fee_bps: float = 0.0,
) -> tuple[float, float, float]:
    """返回 (stake_usdc, shares, fee_usdc);不满足开仓条件时全为 0。"""
    if bankroll <= 0 or not (0.0 < price < 1.0):
        return 0.0, 0.0, 0.0
    frac = min(kelly_fraction(q, price) * kelly_multiplier, max_stake_fraction)
    if frac <= 0.0:
        return 0.0, 0.0, 0.0
    stake = bankroll * frac
    # 含费上限:stake × (1 + fee率×min(p,1−p)/p) ≤ bankroll × max_stake_fraction
    fee_per_stake = (fee_bps / 1e4) * min(price, 1.0 - price) / price
    cap_incl_fee = bankroll * max_stake_fraction / (1.0 + fee_per_stake)
    stake = min(stake, cap_incl_fee)
    shares = stake / price
    return stake, shares, taker_fee(price, shares, fee_bps)
