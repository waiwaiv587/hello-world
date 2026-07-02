"""离线端到端仿真:本机模拟 Polymarket(Gamma/CLOB/WS)与 Binance,
把区间压缩成 1 分钟,让真实主循环完整跑
发现市场 → 快照 → 模拟入场 → 结算 → 报表,全程不出网。

用法:
    python scripts/simulate.py [--cycles 2] [--out data/sim]

第一个市场故意让 Gamma 不出结算结果(验证 Binance K 线兜底),
之后的市场由 Gamma 正常结算(验证官方结果路径)。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polymarket_paper import miniws  # noqa: E402
from polymarket_paper.config import Config  # noqa: E402
from polymarket_paper.main import main_loop  # noqa: E402
from polymarket_paper.report import (  # noqa: E402
    build_html_report, build_text_report, export_csv)
from polymarket_paper.storage import Store  # noqa: E402

INTERVAL_S = 60          # 1 分钟一个市场区间(仿真专用压缩尺度)
log = logging.getLogger("simulate")


class PriceWalker:
    """模拟 BTC 随机游走;线程安全地记录 (ts, price) 序列。"""

    def __init__(self, start: float = 65_000.0, tick_s: float = 0.2,
                 sigma: float = 5e-4, seed: int = 42):
        self.tick_s = tick_s
        self.sigma = sigma
        self.rng = random.Random(seed)
        self.log: list[tuple[float, float]] = [(time.time(), start)]

    @property
    def price(self) -> float:
        return self.log[-1][1]

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.tick_s)
            p = self.price * math.exp(self.rng.gauss(0.0, self.sigma))
            self.log.append((time.time(), p))

    def open_close(self, start: float, end: float) -> tuple[float, float] | None:
        inside = [p for ts, p in self.log if start <= ts <= end]
        return (inside[0], inside[-1]) if inside else None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class MockExchange:
    """Gamma + CLOB REST 的本机替身(单个 HTTP 服务承担两个角色)。"""

    def __init__(self, walker: PriceWalker):
        self.walker = walker
        self.binance_settles: set[str] = set()   # 走 Binance 兜底的市场

    def market_dict(self, end: float) -> dict:
        cond = f"0xsim{int(end)}"
        closed = time.time() > end
        d = {
            "question": f"Bitcoin Up or Down - sim {int(end)}",
            "conditionId": cond,
            "slug": f"btc-sim-{int(end)}",
            "endDate": _iso(end),
            "outcomes": json.dumps(["Up", "Down"]),
            "clobTokenIds": json.dumps([f"u{int(end)}", f"d{int(end)}"]),
            "closed": closed,
        }
        if closed and cond not in self.binance_settles:
            oc = self.walker.open_close(end - INTERVAL_S, end)
            if oc:
                up_won = oc[1] > oc[0]
                d["outcomePrices"] = json.dumps(
                    ["1", "0"] if up_won else ["0", "1"])
        return d

    def handle(self, path: str, params: dict) -> object:
        if path == "/markets":
            if "condition_ids" in params:            # 结算查询
                cond = params["condition_ids"][0]
                end = float(cond.removeprefix("0xsim"))
                return [self.market_dict(end)]
            # 发现查询:返回当前区间市场
            now = time.time()
            end = (int(now) // INTERVAL_S + 1) * INTERVAL_S
            return [self.market_dict(float(end))]
        if path.startswith("/markets/"):             # CLOB taker 费率
            return {"taker_base_fee": 200}
        if path == "/book":                          # CLOB 订单簿快照
            return {"bids": [{"price": "0.49", "size": "500"}],
                    "asks": [{"price": "0.51", "size": "500"}]}
        if path == "/api/v3/klines":                 # Binance K 线
            start = float(params["startTime"][0]) / 1000.0
            end = start + INTERVAL_S
            if time.time() < end:
                return []
            oc = self.walker.open_close(start, end)
            if oc is None:
                return []
            o, c = oc
            return [[int(start * 1000), f"{o}", f"{max(o, c)}",
                     f"{min(o, c)}", f"{c}", "0", int(end * 1000) - 1]]
        return {"error": f"未知路径 {path}"}


def start_http(mock: MockExchange) -> tuple[ThreadingHTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlsplit(self.path)
            body = json.dumps(mock.handle(u.path, parse_qs(u.query))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


async def binance_ws_handler(walker: PriceWalker, ws) -> None:
    """持续推送成交消息。"""
    while True:
        await ws.send(json.dumps({
            "e": "trade", "p": f"{walker.price:.2f}",
            "T": int(time.time() * 1000)}))
        await asyncio.sleep(0.2)


async def clob_ws_handler(ws) -> None:
    """收订阅请求后周期性推送订单簿快照。"""
    sub = json.loads(await ws.recv())
    tokens = sub.get("assets_ids", [])
    while True:
        await ws.send(json.dumps([
            {"event_type": "book", "asset_id": t,
             "bids": [{"price": "0.49", "size": "500"}],
             "asks": [{"price": "0.51", "size": "500"}]} for t in tokens]))
        await asyncio.sleep(2.0)


def make_config(http_port: int, binance_ws: int, clob_ws: int,
                db_path: str) -> Config:
    cfg = Config()
    cfg.db_path = db_path
    base = f"http://127.0.0.1:{http_port}"
    cfg.polymarket.gamma_url = base
    cfg.polymarket.clob_rest_url = base
    cfg.polymarket.clob_ws_url = f"ws://127.0.0.1:{clob_ws}/ws/market"
    cfg.polymarket.interval_minutes = INTERVAL_S // 60
    cfg.binance.rest_url = base
    cfg.binance.ws_url = f"ws://127.0.0.1:{binance_ws}/ws/btcusdt@trade"
    # 压缩时间尺度
    cfg.strategy.decision_interval_s = 2.0
    cfg.strategy.warmup_s = 5.0
    cfg.strategy.cutoff_s = 5.0
    cfg.signal.vol_halflife_s = 30.0
    return cfg


async def run(cycles: int, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(out_dir / "sim.db")
    Path(db_path).unlink(missing_ok=True)

    walker = PriceWalker()
    mock = MockExchange(walker)
    # 第一个到期的市场走 Binance 兜底路径
    first_end = (int(time.time()) // INTERVAL_S + 1) * INTERVAL_S
    mock.binance_settles.add(f"0xsim{first_end}")

    http_server, http_port = start_http(mock)
    bin_ws = await miniws.start_ws_server(
        lambda ws: binance_ws_handler(walker, ws))
    clob_ws = await miniws.start_ws_server(clob_ws_handler)
    cfg = make_config(http_port, bin_ws.sockets[0].getsockname()[1],
                      clob_ws.sockets[0].getsockname()[1], db_path)

    walker_task = asyncio.ensure_future(walker.run())
    loop_task = asyncio.ensure_future(main_loop(cfg))

    deadline = time.time() + (cycles + 2) * INTERVAL_S + 60
    probe = Store(db_path)
    try:
        while time.time() < deadline:
            await asyncio.sleep(3)
            settled = probe.conn.execute(
                "SELECT COUNT(*) AS n FROM markets "
                "WHERE outcome IS NOT NULL").fetchone()["n"]
            if settled >= cycles:
                break
        else:
            log.warning("仿真超时,按已有数据出报表")
    finally:
        loop_task.cancel()
        walker_task.cancel()
        await asyncio.gather(loop_task, walker_task, return_exceptions=True)
        http_server.shutdown()
        bin_ws.close()
        clob_ws.close()
        probe.close()

    store = Store(db_path)
    try:
        print("\n" + "=" * 60)
        print(build_text_report(store, cfg.bankroll.initial_usdc, window=50))
        export_csv(store, out_dir, window=50)
        (out_dir / "report.html").write_text(
            build_html_report(store, cfg.bankroll.initial_usdc, window=50),
            encoding="utf-8")
        n_rec = store.conn.execute(
            "SELECT COUNT(*) AS n FROM records").fetchone()["n"]
        n_settled = store.conn.execute(
            "SELECT COUNT(*) AS n FROM markets WHERE outcome IS NOT NULL"
        ).fetchone()["n"]
        sources = [r["resolution_source"] for r in store.conn.execute(
            "SELECT resolution_source FROM markets "
            "WHERE outcome IS NOT NULL").fetchall()]
        print("=" * 60)
        print(f"仿真完成: 结算市场 {n_settled} 个(结算来源: {sources}),"
              f"预测快照 {n_rec} 条")
        print(f"报表: {out_dir / 'report.html'}")
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="离线端到端仿真")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--out", default="data/sim")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.cycles, Path(args.out)))


if __name__ == "__main__":
    main()
