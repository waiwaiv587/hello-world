import unittest
from pathlib import Path

from polymarket_paper.config import assert_paper_mode, load_config

ROOT = Path(__file__).resolve().parent.parent


class TestConfig(unittest.TestCase):
    def test_repo_config_loads_and_is_paper(self):
        cfg = load_config(ROOT / "config.toml")
        self.assertTrue(cfg.paper)
        self.assertEqual(cfg.bankroll.kelly_multiplier, 0.25)
        self.assertEqual(cfg.bankroll.max_stake_fraction, 0.02)
        self.assertEqual(cfg.strategy.edge_threshold, 0.05)
        # 硬性要求:所有密钥留空
        assert_paper_mode(cfg)

    def test_nonempty_key_refused(self):
        cfg = load_config(ROOT / "config.toml")
        cfg.keys["polymarket_private_key"] = "0xdeadbeef"
        with self.assertRaises(SystemExit):
            assert_paper_mode(cfg)

    def test_live_mode_refused(self):
        cfg = load_config(ROOT / "config.toml")
        cfg.paper = False
        with self.assertRaises(SystemExit):
            assert_paper_mode(cfg)


if __name__ == "__main__":
    unittest.main()
