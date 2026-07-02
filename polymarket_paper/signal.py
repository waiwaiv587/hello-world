"""概率信号:基于剩余时间、价格位移、已实现波动率输出 own_prob。

模型:零漂移几何布朗运动下,
    P(结算价 > 区间开盘价) = Φ( ln(S_t / S_open) / (σ · √τ) )
其中 σ 为每 √秒 的对数收益波动率(EWMA 估计),τ 为剩余秒数。
"""

from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


class EwmaVol:
    """1 秒尺度对数收益方差的指数加权估计器。

    成交更新的间隔不固定:每次观测把平方对数收益除以经过秒数,
    归一成"每秒方差"样本,再按经过时间对应的衰减权重做 EWMA。
    sigma 的单位是 每√秒,与 prob_up 中 √(剩余秒数) 相乘即可。
    """

    def __init__(self, halflife_s: float = 300.0, min_sigma: float = 1e-6):
        if halflife_s <= 0:
            raise ValueError("halflife_s 必须为正")
        self.halflife_s = halflife_s
        self.min_sigma = min_sigma
        self._var_per_s: float | None = None
        self._last_price: float | None = None
        self._last_ts: float | None = None

    def update(self, price: float, ts: float) -> None:
        if price <= 0:
            return
        if self._last_price is not None and self._last_ts is not None and ts > self._last_ts:
            dt = ts - self._last_ts
            r = math.log(price / self._last_price)
            var_obs = (r * r) / dt
            alpha = 1.0 - 0.5 ** (dt / self.halflife_s)
            if self._var_per_s is None:
                self._var_per_s = var_obs
            else:
                self._var_per_s += alpha * (var_obs - self._var_per_s)
        self._last_price = price
        self._last_ts = ts

    @property
    def ready(self) -> bool:
        return self._var_per_s is not None

    @property
    def sigma(self) -> float | None:
        """每 √秒 的波动率;未就绪返回 None。"""
        if self._var_per_s is None:
            return None
        return max(math.sqrt(self._var_per_s), self.min_sigma)


def prob_up(
    price: float,
    open_price: float,
    remaining_s: float,
    sigma: float | None,
    clamp: float = 0.005,
) -> float:
    """自估"本区间上涨"概率。

    - remaining_s <= 0 时按当前位移直接坍缩为 0/1(平盘按 Down,即 0)。
    - 波动率未就绪时返回 0.5(无信息先验),调用方应在 warmup 期内不开仓。
    - 输出裁剪到 [clamp, 1-clamp],避免 Brier/ Kelly 出现退化极值。
    """
    if price <= 0 or open_price <= 0:
        raise ValueError("价格必须为正")
    if remaining_s <= 0:
        return 1.0 if price > open_price else 0.0
    if sigma is None or sigma <= 0:
        return 0.5
    z = math.log(price / open_price) / (sigma * math.sqrt(remaining_s))
    return min(max(norm_cdf(z), clamp), 1.0 - clamp)
