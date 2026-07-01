import unittest

from polymarket_paper.storage import Store
from polymarket_paper.trader import trade_pnl


def make_store() -> Store:
    return Store(":memory:")


class TestStore(unittest.TestCase):
    def test_bankroll_roundtrip(self):
        s = make_store()
        self.assertEqual(s.get_bankroll(10_000.0), 10_000.0)
        s.set_bankroll(9_500.0)
        self.assertEqual(s.get_bankroll(10_000.0), 9_500.0)
        s.close()

    def test_settle_flow(self):
        s = make_store()
        s.upsert_market(
            market_id="cond1", slug="btc-15m", question="Bitcoin Up or Down",
            token_id_up="tu", token_id_down="td",
            interval_start=1000.0, interval_end=1900.0, fee_bps=200.0)
        s.set_market_open_price("cond1", 65_000.0)
        # 一条纯预测快照 + 一笔假想单
        s.insert_record(
            market_id="cond1", own_prob=0.55, mkt_bid_up=0.50, mkt_ask_up=0.52,
            mkt_mid_up=0.51, mkt_ask_down=0.49, btc_price=65_010.0,
            sigma=1e-4, remaining_s=600.0, ts=1300.0)
        s.insert_record(
            market_id="cond1", own_prob=0.62, mkt_bid_up=0.53, mkt_ask_up=0.55,
            mkt_mid_up=0.54, mkt_ask_down=0.46, btc_price=65_050.0,
            sigma=1e-4, remaining_s=500.0, side="UP", entry_price=0.55,
            stake=55.0, shares=100.0, fee=0.9, ts=1400.0)
        self.assertEqual(s.count_trades("cond1"), 1)

        # 到期,Up 中
        self.assertEqual(len(s.unsettled_markets(before_ts=2000.0)), 1)
        pnl = s.settle_market("cond1", close_price=65_100.0, outcome_up=1,
                              source="binance", pnl_fn=trade_pnl)
        self.assertAlmostEqual(pnl, 100 * 0.45 - 0.9)

        settled = s.settled_records()
        self.assertEqual(len(settled), 2)
        self.assertTrue(all(r["outcome"] == 1 for r in settled))
        # 纯预测快照没有 pnl
        self.assertIsNone(settled[0]["pnl"])

        trades = s.settled_trades()
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["pnl"], 44.1)
        self.assertEqual(len(s.unsettled_markets(before_ts=2000.0)), 0)
        s.close()

    def test_upsert_market_keeps_open_price(self):
        s = make_store()
        kwargs = dict(slug="x", question="q", token_id_up="a",
                      token_id_down="b", interval_start=0.0,
                      interval_end=900.0, fee_bps=0.0)
        s.upsert_market(market_id="m1", **kwargs)
        s.set_market_open_price("m1", 123.0)
        s.upsert_market(market_id="m1", **kwargs)   # 重复 upsert 不应清掉开盘价
        row = s.conn.execute(
            "SELECT open_price FROM markets WHERE market_id='m1'").fetchone()
        self.assertEqual(row["open_price"], 123.0)
        s.close()


if __name__ == "__main__":
    unittest.main()
