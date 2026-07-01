"""报表:滚动 Brier、按概率分桶的校准曲线、假想净损益。

用法:
    python -m polymarket_paper.report [--config config.toml] [--out reports/]

文本与 CSV 输出只用标准库;PNG 图表需要 matplotlib(懒加载,没装则
自动跳过并提示)。核对基准:own_prob 的 Brier 是否低于市场中间价的
Brier —— 即是否跑赢"直接抄市场价"。
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .config import load_config
from .metrics import brier_score, calibration_table, rolling_brier
from .storage import Store

# 参考调色板(dataviz 浅色模式,已通过 CVD/对比度校验)
_C = {
    "surface": "#fcfcfb",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "muted": "#898781",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "own": "#2a78d6",     # 系列1:own_prob
    "market": "#1baf7a",  # 系列2:市场价基线(低对比,靠图例+方块标记补偿)
}


def build_text_report(store: Store, initial_bankroll: float,
                      window: int = 200) -> str:
    rows = store.settled_records()
    lines = ["# Polymarket 纸面校准报表", ""]
    if not rows:
        return "\n".join(lines + ["尚无已结算样本。"])

    own = [r["own_prob"] for r in rows]
    mkt = [r["mkt_mid_up"] for r in rows]
    outcomes = [r["outcome"] for r in rows]

    own_brier = brier_score(own, outcomes)
    mkt_brier = brier_score(mkt, outcomes)
    lines += [
        f"已结算预测快照: {len(rows)} 条",
        f"own_prob Brier : {own_brier:.4f}",
        f"市场价   Brier : {mkt_brier:.4f}(基线 =「直接抄市场价」)",
        f"结论: {'✅ 跑赢市场基线' if own_brier < mkt_brier else '❌ 未跑赢市场基线'}",
        "",
        "## 校准曲线(own_prob,10 桶)",
        f"{'桶':>12} {'样本数':>6} {'平均预测':>8} {'实际频率':>8} {'偏差':>8}",
    ]
    for b in calibration_table(own, outcomes):
        lines.append(
            f"[{b.lo:.1f}, {b.hi:.1f}) {b.n:>6} {b.mean_pred:>8.3f} "
            f"{b.empirical:>8.3f} {b.empirical - b.mean_pred:>+8.3f}")

    trades = store.settled_trades()
    lines += ["", "## 假想交易(扣真实点差与 taker 费)"]
    if trades:
        pnl = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        stake_total = sum(t["stake"] for t in trades)
        fee_total = sum(t["fee"] for t in trades)
        lines += [
            f"笔数: {len(trades)}   胜率: {wins / len(trades):.1%}",
            f"净损益: {pnl:+.2f} USDC(投入本金合计 {stake_total:.2f},费用合计 {fee_total:.2f})",
            f"虚拟资金: {initial_bankroll:.2f} → {initial_bankroll + pnl:.2f} USDC",
        ]
    else:
        lines.append("尚无已结算的假想单。")
    lines += ["", f"(滚动 Brier 窗口 = {window},见 rolling_brier.csv / PNG)"]
    return "\n".join(lines)


def export_csv(store: Store, out_dir: Path, window: int = 200) -> None:
    rows = store.settled_records()
    if not rows:
        return
    own = [r["own_prob"] for r in rows]
    mkt = [r["mkt_mid_up"] for r in rows]
    outcomes = [r["outcome"] for r in rows]

    with (out_dir / "rolling_brier.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "ts", "rolling_brier_own", "rolling_brier_market"])
        for i, (r, bo, bm) in enumerate(zip(
                rows, rolling_brier(own, outcomes, window),
                rolling_brier(mkt, outcomes, window))):
            w.writerow([i, r["ts"], f"{bo:.6f}", f"{bm:.6f}"])

    with (out_dir / "calibration.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["series", "bin_lo", "bin_hi", "n", "mean_pred", "empirical"])
        for name, probs in (("own", own), ("market", mkt)):
            for b in calibration_table(probs, outcomes):
                w.writerow([name, b.lo, b.hi, b.n,
                            f"{b.mean_pred:.4f}", f"{b.empirical:.4f}"])


def _style_ax(ax):
    ax.set_facecolor(_C["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_C["axis"])
    ax.grid(True, color=_C["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=_C["muted"], labelsize=9)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_color(_C["muted"])


def _legend(ax):
    leg = ax.legend(frameon=False, fontsize=9, loc="best")
    for text in leg.get_texts():
        text.set_color(_C["ink2"])


def export_charts(store: Store, out_dir: Path, window: int = 200) -> bool:
    """生成校准曲线与滚动 Brier 两张 PNG;matplotlib 缺失时返回 False。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    rows = store.settled_records()
    if not rows:
        return False
    own = [r["own_prob"] for r in rows]
    mkt = [r["mkt_mid_up"] for r in rows]
    outcomes = [r["outcome"] for r in rows]

    # --- 校准曲线(reliability diagram) ---
    fig, ax = plt.subplots(figsize=(6.4, 5.2), facecolor=_C["surface"])
    _style_ax(ax)
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1,
            color=_C["muted"], zorder=1)
    for name, probs, color, marker in (
            ("own_prob", own, _C["own"], "o"),
            ("市场中间价", mkt, _C["market"], "s")):
        table = calibration_table(probs, outcomes)
        ax.plot([b.mean_pred for b in table], [b.empirical for b in table],
                color=color, linewidth=2, marker=marker, markersize=6,
                markeredgecolor=_C["surface"], markeredgewidth=1,
                label=name, zorder=3)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("预测上涨概率(桶内均值)", color=_C["ink2"], fontsize=10)
    ax.set_ylabel("实际上涨频率", color=_C["ink2"], fontsize=10)
    ax.set_title("校准曲线 — 越贴近对角线越好", color=_C["ink"],
                 fontsize=12, loc="left")
    _legend(ax)
    fig.tight_layout()
    fig.savefig(out_dir / "calibration.png", dpi=150,
                facecolor=_C["surface"])
    plt.close(fig)

    # --- 滚动 Brier ---
    fig, ax = plt.subplots(figsize=(7.2, 4.2), facecolor=_C["surface"])
    _style_ax(ax)
    ax.plot(rolling_brier(own, outcomes, window), color=_C["own"],
            linewidth=2, label="own_prob", zorder=3)
    ax.plot(rolling_brier(mkt, outcomes, window), color=_C["market"],
            linewidth=2, label="市场中间价(基线)", zorder=3)
    ax.set_xlabel("已结算预测快照序号", color=_C["ink2"], fontsize=10)
    ax.set_ylabel(f"滚动 Brier(窗口 {window})", color=_C["ink2"], fontsize=10)
    ax.set_title("滚动 Brier — 越低越好", color=_C["ink"], fontsize=12,
                 loc="left")
    _legend(ax)
    fig.tight_layout()
    fig.savefig(out_dir / "rolling_brier.png", dpi=150,
                facecolor=_C["surface"])
    plt.close(fig)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="生成纸面交易校准报表")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--out", default="reports")
    parser.add_argument("--window", type=int, default=200,
                        help="滚动 Brier 窗口(默认 200)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    store = Store(cfg.db_path)
    try:
        text = build_text_report(store, cfg.bankroll.initial_usdc, args.window)
        print(text)
        (out_dir / "report.md").write_text(text + "\n", encoding="utf-8")
        export_csv(store, out_dir, args.window)
        if export_charts(store, out_dir, args.window):
            print(f"\n图表已输出到 {out_dir}/calibration.png、{out_dir}/rolling_brier.png")
        else:
            print("\n(matplotlib 未安装或无样本,已跳过 PNG,仅输出文本与 CSV)")
    finally:
        store.close()


if __name__ == "__main__":
    main()
