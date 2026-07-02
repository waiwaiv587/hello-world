import random
import tempfile
import unittest
from pathlib import Path

from polymarket_paper.report import (
    build_html_report, build_text_report, export_csv)
from polymarket_paper.storage import Store
from polymarket_paper.trader import trade_pnl


def seed_store(n_markets: int = 30) -> Store:
    """合成 n 个已结算市场:每市场若干快照 + 一笔假想单。"""
    rng = random.Random(7)
    s = Store(":memory:")
    for i in range(n_markets):
        mid = f"cond{i}"
        start = 1000.0 + i * 900
        s.upsert_market(market_id=mid, slug=f"m{i}", question="Bitcoin Up or Down",
                        token_id_up="u", token_id_down="d",
                        interval_start=start, interval_end=start + 900,
                        fee_bps=200.0, open_price=65_000.0)
        outcome = rng.random() < 0.5
        for j in range(3):
            p = min(max(rng.gauss(0.7 if outcome else 0.3, 0.15), 0.01), 0.99)
            s.insert_record(
                market_id=mid, own_prob=p, mkt_bid_up=p - 0.02,
                mkt_ask_up=p + 0.02, mkt_mid_up=p, mkt_ask_down=1 - p + 0.02,
                btc_price=65_000.0, sigma=1e-4, remaining_s=900 - j * 300,
                ts=start + j * 300,
                side="UP" if j == 0 else None,
                entry_price=p + 0.02 if j == 0 else None,
                stake=100.0 if j == 0 else None,
                shares=100.0 / (p + 0.02) if j == 0 else None,
                fee=0.5 if j == 0 else None)
        s.settle_market(mid, close_price=65_100.0 if outcome else 64_900.0,
                        outcome_up=int(outcome), source="binance",
                        pnl_fn=trade_pnl)
    return s


class TestReport(unittest.TestCase):
    def test_text_report_contents(self):
        s = seed_store()
        text = build_text_report(s, 10_000.0)
        self.assertIn("own_prob Brier", text)
        self.assertIn("市场价   Brier", text)
        self.assertIn("校准曲线", text)
        self.assertIn("笔数: 30", text)
        s.close()

    def test_empty_store(self):
        s = Store(":memory:")
        self.assertIn("尚无已结算样本", build_text_report(s, 10_000.0))
        s.close()

    def test_html_report(self):
        s = seed_store()
        doc = build_html_report(s, 10_000.0)
        self.assertIn("<!DOCTYPE html>", doc)
        self.assertIn("运行状态", doc)
        self.assertIn("校准曲线", doc)
        self.assertIn("滚动 Brier", doc)
        self.assertIn("<svg", doc)
        self.assertIn("prefers-color-scheme", doc)   # 深色模式
        s.close()

    def test_html_report_empty(self):
        s = Store(":memory:")
        doc = build_html_report(s, 10_000.0)
        self.assertIn("尚无已结算的预测快照", doc)
        self.assertIn("运行状态", doc)   # 空库也能看到虚拟资金/追踪状态
        s.close()

    def test_csv_export(self):
        s = seed_store()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_csv(s, out)
            rb = (out / "rolling_brier.csv").read_text().strip().splitlines()
            self.assertEqual(len(rb), 1 + 90)   # 表头 + 30市场×3快照
            cal = (out / "calibration.csv").read_text()
            self.assertIn("own", cal)
            self.assertIn("market", cal)
        s.close()


if __name__ == "__main__":
    unittest.main()
