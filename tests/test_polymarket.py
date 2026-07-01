import json
import unittest
from datetime import datetime, timezone

from polymarket_paper.feeds.binance import parse_trade
from polymarket_paper.feeds.polymarket import (
    apply_book_message, select_current_market)
from polymarket_paper.settlement import outcome_from_gamma, outcome_from_prices


def gamma_market(question: str, end_iso: str, cond: str = "0xabc",
                 outcomes=("Up", "Down"), tokens=("111", "222")) -> dict:
    return {
        "question": question,
        "conditionId": cond,
        "slug": question.lower().replace(" ", "-"),
        "endDate": end_iso,
        "outcomes": json.dumps(list(outcomes)),
        "clobTokenIds": json.dumps(list(tokens)),
    }


class TestDiscovery(unittest.TestCase):
    # now = 2026-07-01 00:05:00 UTC;当前区间结算于 00:15
    NOW = datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc).timestamp()
    END_ISO = "2026-07-01T00:15:00Z"

    def test_selects_current_interval_market(self):
        markets = [
            gamma_market("Ethereum Up or Down", self.END_ISO, cond="0xeth"),
            gamma_market("Bitcoin Up or Down - Jul 1, 12:15AM ET",
                         self.END_ISO, cond="0xbtc"),
            # 下一个区间的市场,不应选中
            gamma_market("Bitcoin Up or Down - later",
                         "2026-07-01T00:30:00Z", cond="0xnext"),
            # 已过期
            gamma_market("Bitcoin Up or Down - old",
                         "2026-07-01T00:00:00Z", cond="0xold"),
        ]
        info = select_current_market(markets, self.NOW, "Bitcoin Up or Down", 15)
        self.assertIsNotNone(info)
        self.assertEqual(info.condition_id, "0xbtc")
        self.assertEqual(info.token_id_up, "111")
        self.assertEqual(info.token_id_down, "222")
        self.assertEqual(info.interval_end - info.interval_start, 900)
        self.assertEqual(info.interval_end % 900, 0)

    def test_outcome_order_respected(self):
        # outcomes 顺序为 Down/Up 时 token 映射必须跟着换
        m = gamma_market("Bitcoin Up or Down", self.END_ISO,
                         outcomes=("Down", "Up"), tokens=("111", "222"))
        info = select_current_market([m], self.NOW, "Bitcoin Up or Down", 15)
        self.assertEqual(info.token_id_up, "222")
        self.assertEqual(info.token_id_down, "111")

    def test_non_boundary_end_rejected(self):
        m = gamma_market("Bitcoin Up or Down", "2026-07-01T00:07:00Z")
        self.assertIsNone(
            select_current_market([m], self.NOW, "Bitcoin Up or Down", 15))

    def test_no_match_returns_none(self):
        self.assertIsNone(
            select_current_market([], self.NOW, "Bitcoin Up or Down", 15))


class TestBookState(unittest.TestCase):
    def test_snapshot_sets_top(self):
        books = {}
        apply_book_message(books, {
            "event_type": "book", "asset_id": "t1",
            "bids": [{"price": "0.48", "size": "10"},
                     {"price": "0.47", "size": "5"}],
            "asks": [{"price": "0.52", "size": "10"},
                     {"price": "0.53", "size": "5"}],
        })
        self.assertEqual(books["t1"], {"bid": 0.48, "ask": 0.52})

    def test_price_change_improves_top(self):
        books = {"t1": {"bid": 0.48, "ask": 0.52}}
        apply_book_message(books, {
            "event_type": "price_change", "asset_id": "t1",
            "changes": [{"price": "0.49", "size": "3", "side": "BUY"},
                        {"price": "0.51", "size": "2", "side": "SELL"}],
        })
        self.assertEqual(books["t1"], {"bid": 0.49, "ask": 0.51})

    def test_top_removal_invalidates(self):
        books = {"t1": {"bid": 0.48, "ask": 0.52}}
        apply_book_message(books, {
            "event_type": "price_change", "asset_id": "t1",
            "changes": [{"price": "0.48", "size": "0", "side": "BUY"}],
        })
        self.assertIsNone(books["t1"]["bid"])   # 等待快照/REST 轮询修复
        self.assertEqual(books["t1"]["ask"], 0.52)


class TestSettlementParsing(unittest.TestCase):
    def test_outcome_from_prices(self):
        self.assertEqual(outcome_from_prices(100.0, 100.01), 1)
        self.assertEqual(outcome_from_prices(100.0, 99.99), 0)
        self.assertEqual(outcome_from_prices(100.0, 100.0), 0)   # 平盘判 Down
        self.assertEqual(
            outcome_from_prices(100.0, 100.0, tie_resolves_down=False), 1)

    def test_outcome_from_gamma(self):
        m = {"closed": True, "outcomes": '["Up", "Down"]',
             "outcomePrices": '["1", "0"]'}
        self.assertEqual(outcome_from_gamma(m), 1)
        m["outcomePrices"] = '["0", "1"]'
        self.assertEqual(outcome_from_gamma(m), 0)
        self.assertIsNone(outcome_from_gamma({"closed": False}))


class TestBinanceParse(unittest.TestCase):
    def test_trade_message(self):
        msg = json.dumps({"e": "trade", "p": "65000.10", "T": 1782864300123})
        self.assertEqual(parse_trade(msg), (65000.10, 1782864300.123))

    def test_non_trade_ignored(self):
        self.assertIsNone(parse_trade(json.dumps({"e": "ping"})))


if __name__ == "__main__":
    unittest.main()
