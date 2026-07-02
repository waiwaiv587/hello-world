import unittest

from polymarket_paper.metrics import brier_score, calibration_table, rolling_brier


class TestBrier(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(brier_score([1.0, 0.0], [1, 0]), 0.0)

    def test_coin_flip(self):
        self.assertAlmostEqual(brier_score([0.5, 0.5], [1, 0]), 0.25)

    def test_worst(self):
        self.assertEqual(brier_score([0.0, 1.0], [1, 0]), 1.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            brier_score([], [])


class TestRollingBrier(unittest.TestCase):
    def test_window(self):
        probs = [1.0, 1.0, 0.0, 0.0]
        outs = [1, 1, 1, 1]           # 后两个预测完全错
        rb = rolling_brier(probs, outs, window=2)
        self.assertEqual(rb, [0.0, 0.0, 0.5, 1.0])

    def test_expanding_before_window_full(self):
        rb = rolling_brier([0.5, 0.5, 0.5], [1, 0, 1], window=100)
        self.assertAlmostEqual(rb[2], 0.25)


class TestCalibration(unittest.TestCase):
    def test_bins_and_freqs(self):
        probs = [0.05, 0.08, 0.95, 0.92, 0.55]
        outs = [0, 0, 1, 1, 1]
        table = calibration_table(probs, outs, n_bins=10)
        by_lo = {b.lo: b for b in table}
        self.assertEqual(by_lo[0.0].n, 2)
        self.assertEqual(by_lo[0.0].empirical, 0.0)
        self.assertEqual(by_lo[0.9].n, 2)
        self.assertEqual(by_lo[0.9].empirical, 1.0)
        self.assertEqual(by_lo[0.5].n, 1)
        self.assertAlmostEqual(by_lo[0.9].mean_pred, 0.935)

    def test_prob_one_lands_in_last_bin(self):
        table = calibration_table([1.0], [1], n_bins=10)
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0].lo, 0.9)

    def test_empty_bins_skipped(self):
        table = calibration_table([0.5], [1], n_bins=10)
        self.assertEqual(len(table), 1)


if __name__ == "__main__":
    unittest.main()
