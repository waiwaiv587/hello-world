"""报表:滚动 Brier、按概率分桶的校准曲线、假想净损益。

用法:
    python -m polymarket_paper.report [--config config.toml] [--out reports/]

输出(全部纯标准库,零依赖):
- report.html —— 自包含网页报表(图表 + 表格),双击用浏览器打开
- report.md   —— 文本版
- rolling_brier.csv / calibration.csv —— 原始数据

核对基准:own_prob 的 Brier 是否低于市场中间价的 Brier
—— 即是否跑赢"直接抄市场价"。
"""

from __future__ import annotations

import argparse
import csv
import html
import time
from pathlib import Path

from .config import load_config
from .metrics import brier_score, calibration_table, rolling_brier
from .storage import Store
from .svgchart import Series, line_chart


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
    lines += ["", f"(滚动 Brier 窗口 = {window},详见 report.html / CSV)"]
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


# 参考调色板(dataviz):浅色/深色两套,由 prefers-color-scheme 切换
_CSS = """
:root {
  --surface: #fcfcfb; --page: #f9f9f7;
  --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7;
  --series-1: #2a78d6; --series-2: #1baf7a;
  --good: #006300; --bad: #d03b3b;
  --border: rgba(11,11,11,0.10);
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #1a1a19; --page: #0d0d0d;
    --ink: #ffffff; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835;
    --series-1: #3987e5; --series-2: #199e70;
    --good: #0ca30c; --bad: #e66767;
    --border: rgba(255,255,255,0.10);
  }
}
body { background: var(--page); color: var(--ink); margin: 0;
       font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 820px; margin: 0 auto; padding: 32px 24px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
section { background: var(--surface); border: 1px solid var(--border);
          border-radius: 10px; padding: 20px; margin-bottom: 20px; }
h2 { font-size: 15px; margin: 0 0 12px; color: var(--ink); }
dl.kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
         gap: 16px; margin: 0; }
dl.kpi div { border-left: 3px solid var(--grid); padding-left: 12px; }
dl.kpi dt { color: var(--muted); font-size: 12px; margin-bottom: 2px; }
dl.kpi dd { margin: 0; font-size: 20px; font-weight: 600; }
section.status dl.kpi dd { font-size: 16px; font-weight: 500; }
.good { color: var(--good); } .bad { color: var(--bad); }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th { color: var(--muted); font-weight: 500; text-align: right; padding: 6px 10px;
     border-bottom: 1px solid var(--axis); }
td { text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--grid);
     font-variant-numeric: tabular-nums; color: var(--ink2); }
th:first-child, td:first-child { text-align: left; }
p.note { color: var(--muted); font-size: 12px; line-height: 1.6; }
"""


def render_status_bar(status: dict) -> str:
    """运行状态摘要:虚拟资金、追踪市场数、待结算数、最近一条快照时间。

    与"已结算校准数据"分开展示——采集器刚启动、样本还没攒够时,这块也
    能立刻显示出东西,让人确认程序是不是真的在跑。
    """
    last_ts = status["last_record_ts"]
    last_str = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_ts))
                if last_ts else "—")
    return "\n".join([
        '<section class="status"><h2>运行状态</h2>',
        '<dl class="kpi">',
        f'<div><dt>虚拟资金</dt><dd>{status["bankroll"]:.2f} USDC</dd></div>',
        f'<div><dt>追踪市场数</dt><dd>{status["total_markets"]}</dd></div>',
        f'<div><dt>待结算</dt><dd>{status["pending_markets"]}</dd></div>',
        f'<div><dt>最近一条快照</dt><dd>{html.escape(last_str)}</dd></div>',
        "</dl></section>",
    ])


def render_body(store: Store, initial_bankroll: float,
                window: int = 200) -> str:
    """报表主体(不含 <html>/<head> 外壳)。静态报表与实时仪表盘共用,
    仪表盘每次请求都重新调用这个函数,直接反映数据库最新状态。
    """
    parts = [render_status_bar(store.status_summary(initial_bankroll))]
    rows = store.settled_records()
    if not rows:
        parts.append(
            "<section><p>尚无已结算的预测快照,采集器可能还在热身或刚"
            "启动。这块会随数据库更新自动出现内容,无需手动操作。</p>"
            "</section>")
        return "\n".join(parts)

    own = [r["own_prob"] for r in rows]
    mkt = [r["mkt_mid_up"] for r in rows]
    outcomes = [r["outcome"] for r in rows]
    own_brier = brier_score(own, outcomes)
    mkt_brier = brier_score(mkt, outcomes)
    beat = own_brier < mkt_brier

    trades = store.settled_trades()
    pnl = sum(t["pnl"] for t in trades) if trades else 0.0
    wins = sum(1 for t in trades if t["pnl"] > 0) if trades else 0

    # ---- 摘要 ----
    verdict = ('<span class="good">跑赢市场基线 ✓</span>' if beat
               else '<span class="bad">未跑赢市场基线 ✗</span>')
    pnl_cls = "good" if pnl >= 0 else "bad"
    parts += [
        "<section><h2>两个关键数字</h2>",
        '<dl class="kpi">',
        f"<div><dt>own_prob Brier(越低越好)</dt><dd>{own_brier:.4f}</dd></div>",
        f"<div><dt>市场价 Brier(基线)</dt><dd>{mkt_brier:.4f}</dd></div>",
        f"<div><dt>校准对比</dt><dd>{verdict}</dd></div>",
        f'<div><dt>扣费后假想净损益</dt><dd class="{pnl_cls}">{pnl:+.2f} USDC</dd></div>',
        "</dl>",
        f'<p class="note">样本:已结算预测快照 {len(rows)} 条;假想单 {len(trades)} 笔'
        + (f",胜率 {wins / len(trades):.1%}" if trades else "")
        + f";虚拟资金 {initial_bankroll:.2f} → {initial_bankroll + pnl:.2f} USDC。</p>",
        "</section>",
    ]

    # ---- 校准曲线 ----
    def cal_series(name: str, probs: list[float], color: str,
                   marker: str) -> Series:
        table = calibration_table(probs, outcomes)
        return Series(
            name=name, color_var=color, marker=marker,
            points=[(b.mean_pred, b.empirical) for b in table],
            tooltips=[f"{name} [{b.lo:.1f},{b.hi:.1f}): 预测 {b.mean_pred:.3f},"
                      f"实际 {b.empirical:.3f},n={b.n}" for b in table])

    parts += ["<section><h2>校准曲线(reliability diagram)</h2>",
              line_chart(
                  [cal_series("own_prob", own, "--series-1", "circle"),
                   cal_series("市场中间价", mkt, "--series-2", "square")],
                  title="校准曲线", subtitle="越贴近虚线对角线,校准越好(悬停看每桶样本数)",
                  x_label="预测上涨概率(桶内均值)", y_label="实际上涨频率",
                  x_range=(0.0, 1.0), y_range=(0.0, 1.0), diagonal=True),
              "</section>"]

    # ---- 滚动 Brier ----
    rb_own = rolling_brier(own, outcomes, window)
    rb_mkt = rolling_brier(mkt, outcomes, window)
    y_hi = max(max(rb_own), max(rb_mkt), 0.3)
    parts += ["<section><h2>滚动 Brier</h2>",
              line_chart(
                  [Series("own_prob", list(enumerate(rb_own)), "--series-1"),
                   Series("市场中间价(基线)", list(enumerate(rb_mkt)),
                          "--series-2")],
                  title="滚动 Brier", subtitle="越低越好;蓝线持续压在青线下方 = 有真实校准优势",
                  x_label="已结算预测快照序号",
                  y_label=f"Brier(窗口 {window})",
                  y_range=(0.0, y_hi * 1.05)),
              "</section>"]

    # ---- 校准表格(图表的表格视图) ----
    parts += ["<section><h2>校准明细(own_prob,10 桶)</h2>", "<table>",
              "<tr><th>桶</th><th>样本数</th><th>平均预测</th>"
              "<th>实际频率</th><th>偏差</th></tr>"]
    for b in calibration_table(own, outcomes):
        parts.append(
            f"<tr><td>[{b.lo:.1f}, {b.hi:.1f})</td><td>{b.n}</td>"
            f"<td>{b.mean_pred:.3f}</td><td>{b.empirical:.3f}</td>"
            f"<td>{b.empirical - b.mean_pred:+.3f}</td></tr>")
    parts += ["</table>", "</section>"]

    parts += [
        "<section><h2>说明</h2>",
        '<p class="note">纸面模式,全程无真实下单。假想单以对手方卖一价成交'
        "(点差成本已含),并扣除 taker 费。市场价基线 = 快照时刻 Up 合约的"
        "买卖中间价。决策口径:|own_prob − 卖一价| 严格大于 5 个百分点才记单,"
        "1/4 Kelly,单笔(本金+费)≤ 虚拟资金 2%。</p></section>",
    ]
    return "\n".join(parts)


def build_html_report(store: Store, initial_bankroll: float,
                      window: int = 200) -> str:
    """静态报表:一次性生成的完整 HTML 文件(report.html)。"""
    body = render_body(store, initial_bankroll, window)
    return "\n".join([
        "<!DOCTYPE html>", '<html lang="zh"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Polymarket 纸面校准报表</title>",
        f"<style>{_CSS}</style></head><body><main>",
        "<h1>Polymarket 纸面校准报表</h1>",
        f'<div class="sub">生成于 {html.escape(time.strftime("%Y-%m-%d %H:%M:%S"))}'
        f" · 滚动窗口 {window}</div>",
        body,
        "</main></body></html>",
    ])


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
        html_doc = build_html_report(store, cfg.bankroll.initial_usdc,
                                     args.window)
        (out_dir / "report.html").write_text(html_doc, encoding="utf-8")
        print(f"\n网页报表: {out_dir / 'report.html'}(浏览器打开)")
    finally:
        store.close()


if __name__ == "__main__":
    main()
