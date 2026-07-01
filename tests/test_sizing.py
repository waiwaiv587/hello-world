import unittest

from polymarket_paper.sizing import kelly_fraction, size_position, taker_fee


class TestKelly(unittest.TestCase):
    def test_known_value(self):
        # q=0.6, p=0.5 → f* = 0.1/0.5 = 0.2
        self.assertAlmostEqual(kelly_fraction(0.6, 0.5), 0.2)

    def test_no_edge_no_bet(self):
        self.assertEqual(kelly_fraction(0.5, 0.5), 0.0)
        self.assertEqual(kelly_fraction(0.4, 0.5), 0.0)

    def test_invalid_price(self):
        self.assertEqual(kelly_fraction(0.6, 0.0), 0.0)
        self.assertEqual(kelly_fraction(0.6, 1.0), 0.0)


class TestFee(unittest.TestCase):
    def test_formula(self):
        # 200bps × min(0.4, 0.6) × 100 股 = 0.02 × 0.4 × 100 = 0.8
        self.assertAlmostEqual(taker_fee(0.4, 100.0, 200.0), 0.8)

    def test_symmetric_in_price(self):
        self.assertAlmostEqual(taker_fee(0.3, 10, 200), taker_fee(0.7, 10, 200))


class TestSizePosition(unittest.TestCase):
    def test_quarter_kelly_below_cap(self):
        # f* = 0.2 → 1/4 Kelly = 0.05 > 2% 上限 → 触顶
        stake, shares, fee = size_position(0.6, 0.5, 10_000.0)
        self.assertAlmostEqual(stake, 200.0)      # 2% × 10000(无费)
        self.assertAlmostEqual(shares, 400.0)

    def test_small_edge_uses_kelly_not_cap(self):
        # q=0.53, p=0.5 → f* = 0.06 → 1/4 = 1.5% < 2%
        stake, _, _ = size_position(0.53, 0.5, 10_000.0)
        self.assertAlmostEqual(stake, 150.0)

    def test_cap_includes_fee(self):
        bankroll, cap = 10_000.0, 0.02
        stake, shares, fee = size_position(
            0.9, 0.5, bankroll, kelly_multiplier=1.0,
            max_stake_fraction=cap, fee_bps=200.0)
        self.assertLessEqual(stake + fee, bankroll * cap + 1e-9)
        self.assertAlmostEqual(taker_fee(0.5, shares, 200.0), fee)

    def test_no_bet_cases(self):
        self.assertEqual(size_position(0.4, 0.5, 10_000.0), (0.0, 0.0, 0.0))
        self.assertEqual(size_position(0.6, 0.5, 0.0), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
