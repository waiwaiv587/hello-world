import unittest

from polymarket_paper.config import Config
from polymarket_paper.trader import decide, trade_pnl


def make_cfg() -> Config:
    return Config()


class TestDecide(unittest.TestCase):
    def test_no_trade_when_edge_at_threshold(self):
        # edge 恰好 5pp 不开仓(要求严格大于)
        cfg = make_cfg()
        self.assertIsNone(decide(0.55, 0.50, 0.50, 10_000, cfg, 0.0))

    def test_buys_underpriced_up(self):
        cfg = make_cfg()
        intent = decide(0.62, 0.55, 0.46, 10_000, cfg, 0.0)
        self.assertIsNotNone(intent)
        self.assertEqual(intent.side, "UP")
        self.assertAlmostEqual(intent.edge, 0.07)
        self.assertAlmostEqual(intent.price, 0.55)

    def test_buys_underpriced_down(self):
        cfg = make_cfg()
        # own_prob_up=0.30 → down 概率 0.70,down 卖一 0.60 → edge 0.10
        intent = decide(0.30, 0.32, 0.60, 10_000, cfg, 0.0)
        self.assertEqual(intent.side, "DOWN")
        self.assertAlmostEqual(intent.edge, 0.10)

    def test_picks_larger_edge_side(self):
        cfg = make_cfg()
        # up edge = 0.62-0.54 = 0.08;down edge = 0.38-0.28 = 0.10 → DOWN
        intent = decide(0.62, 0.54, 0.28, 10_000, cfg, 0.0)
        self.assertEqual(intent.side, "DOWN")

    def test_missing_book_side_handled(self):
        cfg = make_cfg()
        intent = decide(0.70, 0.60, None, 10_000, cfg, 0.0)
        self.assertEqual(intent.side, "UP")
        self.assertIsNone(decide(0.70, None, None, 10_000, cfg, 0.0))

    def test_min_stake_filter(self):
        cfg = make_cfg()
        cfg.strategy.min_stake_usdc = 1.0
        # 虚拟资金太小 → 本金低于 1 USDC → 不记单
        self.assertIsNone(decide(0.70, 0.60, None, 10.0, cfg, 0.0))


class TestTradePnl(unittest.TestCase):
    def test_win_up(self):
        # 100 股 @0.55,费 0.5,Up 中 → 100×0.45 − 0.5 = 44.5
        self.assertAlmostEqual(trade_pnl("UP", 0.55, 100, 0.5, 1), 44.5)

    def test_lose_up(self):
        self.assertAlmostEqual(trade_pnl("UP", 0.55, 100, 0.5, 0), -55.5)

    def test_win_down(self):
        self.assertAlmostEqual(trade_pnl("DOWN", 0.40, 100, 0.5, 0), 59.5)

    def test_lose_down(self):
        self.assertAlmostEqual(trade_pnl("DOWN", 0.40, 100, 0.5, 1), -40.5)


if __name__ == "__main__":
    unittest.main()
