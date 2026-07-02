"""纯标准库 SVG 折线图,嵌入 report.html 使用。

颜色一律引用 CSS 变量(--series-1 等),由 HTML 页面按浅色/深色模式
统一定义;标记点带 <title> 原生悬停提示;每张图单轴、细网格、图例。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from xml.sax.saxutils import escape


@dataclass
class Series:
    name: str
    points: list[tuple[float, float]]
    color_var: str                      # 如 "--series-1"
    marker: str | None = None           # "circle" / "square" / None
    tooltips: list[str] = field(default_factory=list)


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """在 [lo, hi] 上取"好看"的刻度(1/2/5×10^k 步长)。"""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    start = math.ceil(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 1e-9:
        ticks.append(round(t, 10))
        t += step
    return ticks


def line_chart(
    series: list[Series],
    *,
    title: str,
    subtitle: str = "",
    x_label: str,
    y_label: str,
    x_range: tuple[float, float] | None = None,
    y_range: tuple[float, float] | None = None,
    diagonal: bool = False,
    width: int = 720,
    height: int = 460,
) -> str:
    """返回一段 <svg>…</svg> 字符串。"""
    pad_l, pad_r, pad_t, pad_b = 56, 20, 64, 48
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    xs = [p[0] for s in series for p in s.points]
    ys = [p[1] for s in series for p in s.points]
    x_lo, x_hi = x_range if x_range else (min(xs, default=0), max(xs, default=1))
    y_lo, y_hi = y_range if y_range else (min(ys, default=0), max(ys, default=1))
    if x_hi <= x_lo:
        x_hi = x_lo + 1.0
    if y_hi <= y_lo:
        y_hi = y_lo + 1.0

    def sx(x: float) -> float:
        return pad_l + (x - x_lo) / (x_hi - x_lo) * plot_w

    def sy(y: float) -> float:
        return pad_t + plot_h - (y - y_lo) / (y_hi - y_lo) * plot_h

    out: list[str] = []
    out.append(
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        ' xmlns="http://www.w3.org/2000/svg" role="img"'
        f' aria-label="{escape(title)}" style="max-width:100%;height:auto">')
    out.append(f'<rect width="{width}" height="{height}" fill="var(--surface)"/>')

    # 标题(左对齐)与副标题
    out.append(f'<text x="{pad_l}" y="24" fill="var(--ink)" font-size="15" '
               f'font-weight="600">{escape(title)}</text>')
    if subtitle:
        out.append(f'<text x="{pad_l}" y="42" fill="var(--ink2)" '
                   f'font-size="12">{escape(subtitle)}</text>')

    # 网格 + 刻度(细线,压在数据下方)
    for t in nice_ticks(y_lo, y_hi):
        if not (y_lo - 1e-9 <= t <= y_hi + 1e-9):
            continue
        y = sy(t)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{y:.1f}" stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" fill="var(--muted)" '
                   f'font-size="11" text-anchor="end">{t:g}</text>')
    for t in nice_ticks(x_lo, x_hi):
        if not (x_lo - 1e-9 <= t <= x_hi + 1e-9):
            continue
        x = sx(t)
        out.append(f'<text x="{x:.1f}" y="{pad_t + plot_h + 18}" '
                   f'fill="var(--muted)" font-size="11" '
                   f'text-anchor="middle">{t:g}</text>')

    # 坐标轴基线
    out.append(f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" '
               f'y2="{pad_t + plot_h}" stroke="var(--axis)" stroke-width="1"/>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" '
               f'y2="{pad_t + plot_h}" stroke="var(--axis)" stroke-width="1"/>')

    # 对角参考线(校准图:完美校准)
    if diagonal:
        out.append(f'<line x1="{sx(x_lo):.1f}" y1="{sy(y_lo):.1f}" '
                   f'x2="{sx(x_hi):.1f}" y2="{sy(y_hi):.1f}" '
                   'stroke="var(--muted)" stroke-width="1" '
                   'stroke-dasharray="5 4"/>')

    # 数据线 + 标记
    for s in series:
        if not s.points:
            continue
        pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in s.points)
        out.append(f'<polyline points="{pts}" fill="none" '
                   f'stroke="var({s.color_var})" stroke-width="2" '
                   'stroke-linejoin="round" stroke-linecap="round"/>')
        if s.marker:
            for i, (x, y) in enumerate(s.points):
                tip = s.tooltips[i] if i < len(s.tooltips) else \
                    f"{s.name}: ({x:g}, {y:g})"
                title_el = f"<title>{escape(tip)}</title>"
                if s.marker == "square":
                    out.append(
                        f'<rect x="{sx(x) - 4:.1f}" y="{sy(y) - 4:.1f}" '
                        f'width="8" height="8" rx="1.5" '
                        f'fill="var({s.color_var})" stroke="var(--surface)" '
                        f'stroke-width="2">{title_el}</rect>')
                else:
                    out.append(
                        f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4.5" '
                        f'fill="var({s.color_var})" stroke="var(--surface)" '
                        f'stroke-width="2">{title_el}</circle>')

    # 轴标签
    out.append(f'<text x="{pad_l + plot_w / 2:.0f}" y="{height - 10}" '
               f'fill="var(--ink2)" font-size="12" '
               f'text-anchor="middle">{escape(x_label)}</text>')
    out.append(f'<text x="16" y="{pad_t + plot_h / 2:.0f}" fill="var(--ink2)" '
               f'font-size="12" text-anchor="middle" '
               f'transform="rotate(-90 16 {pad_t + plot_h / 2:.0f})">'
               f'{escape(y_label)}</text>')

    # 图例(右上,色块 + 文本墨色)
    lx = pad_l + plot_w
    for s in reversed(series):
        label_w = 12 + 7 * _display_width(s.name) + 16
        lx -= label_w
        out.append(f'<rect x="{lx}" y="16" width="12" height="12" rx="2" '
                   f'fill="var({s.color_var})"/>')
        out.append(f'<text x="{lx + 16}" y="26" fill="var(--ink2)" '
                   f'font-size="12">{escape(s.name)}</text>')

    out.append("</svg>")
    return "\n".join(out)


def _display_width(text: str) -> int:
    """粗略估算显示宽度:全角字符按 2 个半角算(仅用于图例排版)。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
