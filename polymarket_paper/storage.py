"""SQLite 落库。

records 表覆盖规格要求的字段:时间戳、市场ID、own_prob、市场价、
假想仓位、结算结果;markets 表记录每个市场区间(默认 5 分钟)的元数据与结算。
无假想单的预测快照也入库(side 为 NULL)——校准曲线需要全部预测样本。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id       TEXT PRIMARY KEY,   -- Polymarket condition_id
    slug            TEXT,
    question        TEXT,
    token_id_up     TEXT,
    token_id_down   TEXT,
    interval_start  REAL,               -- epoch 秒
    interval_end    REAL,
    open_price      REAL,               -- Binance 区间开盘参照价
    close_price     REAL,
    fee_bps         REAL,
    outcome         INTEGER,            -- 1=Up, 0=Down, NULL=未结算
    resolution_source TEXT              -- 'polymarket' / 'binance'
);
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,          -- 快照时间戳(epoch 秒)
    market_id   TEXT NOT NULL,
    own_prob    REAL NOT NULL,          -- 自估上涨概率
    mkt_bid_up  REAL,
    mkt_ask_up  REAL,
    mkt_mid_up  REAL,                   -- 市场价(基线预测)
    mkt_ask_down REAL,
    btc_price   REAL,
    sigma       REAL,
    remaining_s REAL,
    side        TEXT,                   -- 'UP'/'DOWN',NULL=仅预测未开仓
    entry_price REAL,
    stake       REAL,                   -- 假想仓位本金(USDC)
    shares      REAL,
    fee         REAL,
    outcome     INTEGER,                -- 结算结果:1=Up, 0=Down
    pnl         REAL                    -- 假想净损益(含费),无单为 NULL
);
CREATE INDEX IF NOT EXISTS idx_records_market ON records(market_id);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value REAL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- 虚拟资金 ----

    def get_bankroll(self, initial: float) -> float:
        row = self.conn.execute(
            "SELECT value FROM state WHERE key='bankroll'").fetchone()
        if row is None:
            self.set_bankroll(initial)
            return initial
        return float(row["value"])

    def set_bankroll(self, value: float) -> None:
        self.conn.execute(
            "INSERT INTO state(key, value) VALUES('bankroll', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
        self.conn.commit()

    # ---- 市场 ----

    def upsert_market(self, *, market_id: str, slug: str, question: str,
                      token_id_up: str, token_id_down: str,
                      interval_start: float, interval_end: float,
                      fee_bps: float, open_price: float | None = None) -> None:
        self.conn.execute(
            """INSERT INTO markets(market_id, slug, question, token_id_up,
                 token_id_down, interval_start, interval_end, fee_bps, open_price)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(market_id) DO UPDATE SET
                 open_price=COALESCE(excluded.open_price, markets.open_price),
                 fee_bps=excluded.fee_bps""",
            (market_id, slug, question, token_id_up, token_id_down,
             interval_start, interval_end, fee_bps, open_price))
        self.conn.commit()

    def set_market_open_price(self, market_id: str, open_price: float) -> None:
        self.conn.execute(
            "UPDATE markets SET open_price=? WHERE market_id=?",
            (open_price, market_id))
        self.conn.commit()

    # ---- 预测/假想单 ----

    def insert_record(self, *, market_id: str, own_prob: float,
                      mkt_bid_up: float | None, mkt_ask_up: float | None,
                      mkt_mid_up: float | None, mkt_ask_down: float | None,
                      btc_price: float | None, sigma: float | None,
                      remaining_s: float, side: str | None = None,
                      entry_price: float | None = None,
                      stake: float | None = None, shares: float | None = None,
                      fee: float | None = None, ts: float | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO records(ts, market_id, own_prob, mkt_bid_up,
                 mkt_ask_up, mkt_mid_up, mkt_ask_down, btc_price, sigma,
                 remaining_s, side, entry_price, stake, shares, fee)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts if ts is not None else time.time(), market_id, own_prob,
             mkt_bid_up, mkt_ask_up, mkt_mid_up, mkt_ask_down, btc_price,
             sigma, remaining_s, side, entry_price, stake, shares, fee))
        self.conn.commit()
        return int(cur.lastrowid)

    def count_trades(self, market_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM records "
            "WHERE market_id=? AND side IS NOT NULL", (market_id,)).fetchone()
        return int(row["n"])

    # ---- 结算 ----

    def settle_market(self, market_id: str, *, close_price: float | None,
                      outcome_up: int, source: str,
                      pnl_fn) -> float:
        """结算市场:回填所有 records 的 outcome 与 pnl,返回本市场净损益合计。

        pnl_fn(side, entry_price, shares, fee, outcome_up) -> float
        """
        self.conn.execute(
            "UPDATE markets SET close_price=?, outcome=?, resolution_source=? "
            "WHERE market_id=?", (close_price, outcome_up, source, market_id))
        rows = self.conn.execute(
            "SELECT id, side, entry_price, shares, fee FROM records "
            "WHERE market_id=?", (market_id,)).fetchall()
        total = 0.0
        for row in rows:
            pnl = None
            if row["side"] is not None:
                pnl = pnl_fn(row["side"], row["entry_price"], row["shares"],
                             row["fee"], outcome_up)
                total += pnl
            self.conn.execute(
                "UPDATE records SET outcome=?, pnl=? WHERE id=?",
                (outcome_up, pnl, row["id"]))
        self.conn.commit()
        return total

    # ---- 报表查询 ----

    def settled_records(self) -> list[sqlite3.Row]:
        """所有已结算的预测快照,按时间排序。"""
        return self.conn.execute(
            "SELECT * FROM records WHERE outcome IS NOT NULL "
            "AND mkt_mid_up IS NOT NULL ORDER BY ts").fetchall()

    def settled_trades(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM records WHERE outcome IS NOT NULL "
            "AND side IS NOT NULL ORDER BY ts").fetchall()

    def unsettled_markets(self, before_ts: float) -> list[sqlite3.Row]:
        """已过结算时间但尚未结算的市场。"""
        return self.conn.execute(
            "SELECT * FROM markets WHERE outcome IS NULL AND interval_end < ?",
            (before_ts,)).fetchall()
