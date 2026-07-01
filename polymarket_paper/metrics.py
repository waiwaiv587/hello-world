"""校准指标:Brier、滚动 Brier、按概率分桶的校准曲线。

核心对照:own_prob 的 Brier 是否低于"直接抄市场价"(市场中间价)的 Brier。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """均方概率误差;probs 与 outcomes(0/1)一一对应。"""
    if len(probs) != len(outcomes):
        raise ValueError("长度不一致")
    if not probs:
        raise ValueError("空样本")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def rolling_brier(
    probs: Sequence[float], outcomes: Sequence[int], window: int = 200
) -> list[float]:
    """滑动窗口 Brier;第 i 项是截至第 i 个样本(含)最近 window 个的均值。"""
    out: list[float] = []
    acc = 0.0
    sq = [(p - o) ** 2 for p, o in zip(probs, outcomes)]
    for i, v in enumerate(sq):
        acc += v
        if i >= window:
            acc -= sq[i - window]
        out.append(acc / min(i + 1, window))
    return out


@dataclass
class CalibrationBin:
    lo: float
    hi: float
    n: int
    mean_pred: float      # 桶内平均预测概率
    empirical: float      # 桶内实际上涨频率


def calibration_table(
    probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10
) -> list[CalibrationBin]:
    """按预测概率等宽分桶;空桶跳过。完美校准时 mean_pred ≈ empirical。"""
    if n_bins <= 0:
        raise ValueError("n_bins 必须为正")
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        buckets[idx].append((p, o))
    table: list[CalibrationBin] = []
    for i, rows in enumerate(buckets):
        if not rows:
            continue
        n = len(rows)
        table.append(CalibrationBin(
            lo=i / n_bins,
            hi=(i + 1) / n_bins,
            n=n,
            mean_pred=sum(p for p, _ in rows) / n,
            empirical=sum(o for _, o in rows) / n,
        ))
    return table
