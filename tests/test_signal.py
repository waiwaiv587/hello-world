import math
import unittest

from polymarket_paper.signal import EwmaVol, norm_cdf, prob_up


class TestProbUp(unittest.TestCase):
    def test_at_open_is_half(self):
        self.assertAlmostEqual(prob_up(100.0, 100.0, 300, 0.001), 0.5)

    def test_monotonic_in_displacement(self):
        p_lo = prob_up(99.0, 100.0, 300, 0.001)
        p_mid = prob_up(100.0, 100.0, 300, 0.001)
        p_hi = prob_up(101.0, 100.0, 300, 0.001)
        self.assertLess(p_lo, p_mid)
        self.assertLess(p_mid, p_hi)

    def test_more_time_dilutes_signal(self):
        # 同样的位移,剩余时间越长,概率越靠近 0.5
        near = prob_up(100.5, 100.0, 30, 0.001)
        far = prob_up(100.5, 100.0, 800, 0.001)
        self.assertGreater(near, far)
        self.assertGreater(far, 0.5)

    def test_expiry_collapse(self):
        self.assertEqual(prob_up(100.01, 100.0, 0, 0.001), 1.0)
        self.assertEqual(prob_up(99.99, 100.0, 0, 0.001), 0.0)
        # 平盘按 Down
        self.assertEqual(prob_up(100.0, 100.0, 0, 0.001), 0.0)

    def test_clamp(self):
        p = prob_up(150.0, 100.0, 1, 1e-6, clamp=0.005)
        self.assertEqual(p, 0.995)
        p = prob_up(50.0, 100.0, 1, 1e-6, clamp=0.005)
        self.assertEqual(p, 0.005)

    def test_no_vol_returns_half(self):
        self.assertEqual(prob_up(101.0, 100.0, 300, None), 0.5)

    def test_known_value(self):
        # z = ln(101/100) / (0.001·√100) = 0.99503…,Φ(z) 手工核对
        p = prob_up(101.0, 100.0, 100, 0.001)
        z = math.log(1.01) / (0.001 * 10)
        self.assertAlmostEqual(p, norm_cdf(z))


class TestEwmaVol(unittest.TestCase):
    def test_not_ready_before_two_obs(self):
        v = EwmaVol()
        self.assertFalse(v.ready)
        v.update(100.0, 0.0)
        self.assertFalse(v.ready)
        v.update(100.1, 1.0)
        self.assertTrue(v.ready)

    def test_converges_to_constant_vol(self):
        # 合成序列:每秒对数收益恒为 r → 每秒方差应收敛到 r²
        v = EwmaVol(halflife_s=10.0)
        price, r = 100.0, 1e-4
        for t in range(1, 500):
            price *= math.exp(r)
            v.update(price, float(t))
        self.assertAlmostEqual(v.sigma, r, delta=r * 0.05)

    def test_dt_normalization(self):
        # 同样的每秒方差,用 2 秒间隔观测也应得到一致的 sigma
        v = EwmaVol(halflife_s=10.0)
        price, r = 100.0, 1e-4
        for t in range(2, 1000, 2):
            price *= math.exp(r * 2)          # 2 秒累计收益
            v.update(price, float(t))
        # 每 2 秒收益 2r → 方差 (2r)²/2 = 2r² → sigma = √2·r
        self.assertAlmostEqual(v.sigma, math.sqrt(2) * r, delta=r * 0.1)

    def test_ignores_stale_and_bad_ticks(self):
        v = EwmaVol()
        v.update(100.0, 10.0)
        v.update(-5.0, 11.0)      # 非法价被忽略
        v.update(100.1, 10.0)     # 时间未前进被忽略
        self.assertFalse(v.ready)


if __name__ == "__main__":
    unittest.main()
