import http.client
import random
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from polymarket_paper.config import Config
from polymarket_paper.dashboard import _make_handler, render_page
from polymarket_paper.storage import Store
from polymarket_paper.trader import trade_pnl


def seed_db(db_path: str, n_markets: int = 5) -> None:
    rng = random.Random(3)
    s = Store(db_path)
    for i in range(n_markets):
        mid = f"c{i}"
        start = 1000.0 + i * 300
        s.upsert_market(market_id=mid, slug=f"m{i}", question="Bitcoin Up or Down",
                        token_id_up="u", token_id_down="d", interval_start=start,
                        interval_end=start + 300, fee_bps=200.0, open_price=65_000.0)
        outcome = rng.random() < 0.5
        s.insert_record(market_id=mid, own_prob=0.6 if outcome else 0.4,
                        mkt_bid_up=0.48, mkt_ask_up=0.52, mkt_mid_up=0.5,
                        mkt_ask_down=0.52, btc_price=65_000.0, sigma=1e-4,
                        remaining_s=200.0, ts=start,
                        side="UP", entry_price=0.52, stake=50.0,
                        shares=50.0 / 0.52, fee=0.2)
        s.settle_market(mid, close_price=None, outcome_up=int(outcome),
                        source="binance", pnl_fn=trade_pnl)
    s.close()


class TestRenderPage(unittest.TestCase):
    def test_contains_shell_and_live_indicator(self):
        page = render_page("<p>hi</p>", interval_s=5.0)
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("实时更新中", page)
        self.assertIn("每 5s 刷新一次", page)
        self.assertIn("setInterval(dashRefresh, 5000)", page)
        self.assertIn("<p>hi</p>", page)

    def test_no_leftover_format_placeholders(self):
        # 用 .replace 而不是 .format,确认没有把字面量 JS 花括号搞坏
        page = render_page("<p>x</p>", interval_s=2.5)
        self.assertIn("function dashRefresh()", page)
        self.assertNotIn("__INTERVAL_MS__", page)


class TestDashboardServer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._testMethodName + "_dash.db")
        self.tmp.unlink(missing_ok=True)
        seed_db(str(self.tmp))
        cfg = Config()
        cfg.db_path = str(self.tmp)
        handler = _make_handler(cfg, window=50, interval_s=3.0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.tmp.unlink(missing_ok=True)

    def _get(self, path: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        return resp.status, body

    def test_root_serves_full_page_with_live_data(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn("运行状态", body)
        self.assertIn("追踪市场数", body)
        self.assertIn("校准曲线", body)   # 5 个已结算市场,主体内容应该出现

    def test_fragment_returns_body_only(self):
        status, body = self._get("/fragment")
        self.assertEqual(status, 200)
        self.assertNotIn("<!DOCTYPE html>", body)
        self.assertIn("运行状态", body)

    def test_unknown_path_404(self):
        status, _ = self._get("/nope")
        self.assertEqual(status, 404)

    def test_fragment_reflects_new_data_without_restart(self):
        _, before = self._get("/fragment")
        self.assertIn("追踪市场数</dt><dd>5</dd>", before)

        s = Store(str(self.tmp))
        s.upsert_market(market_id="new1", slug="new", question="Bitcoin Up or Down",
                        token_id_up="u", token_id_down="d", interval_start=5000.0,
                        interval_end=5300.0, fee_bps=200.0)
        s.close()

        _, after = self._get("/fragment")
        self.assertIn("追踪市场数</dt><dd>6</dd>", after)


if __name__ == "__main__":
    unittest.main()
