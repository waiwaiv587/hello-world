"""本地实时仪表盘:自包含 HTTP 服务,浏览器打开后自动定时刷新。

用法:
    python -m polymarket_paper.dashboard [--config config.toml]
                                          [--port 8765] [--interval 8]

只监听 127.0.0.1(不对外网暴露)。数据来自 SQLite(WAL 模式下支持与
main.py 采集器并发读写),每次浏览器请求都读最新数据,不需要重启、不需
要手动重新生成报表——可以和 main.py 同时开着,开一个浏览器标签页放着看。
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config, load_config
from .report import _CSS, render_body
from .storage import Store

_EXTRA_CSS = """
.livebar { display:flex; align-items:center; gap:8px; margin-bottom:24px;
           color: var(--ink2); font-size: 13px; }
.dot { width:8px; height:8px; border-radius:50%; background:var(--good);
       flex: none; animation: dash-pulse 1.6s ease-in-out infinite; }
@keyframes dash-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
"""

# {{}} 均为字面量 JS 花括号,__INTERVAL_MS__ 是唯一的替换点(用 .replace
# 而非 .format/f-string,避免和 JS/CSS 里大量的花括号打架)。
_SCRIPT = """
<script>
function dashRefresh() {
  fetch('/fragment').then(function (r) { return r.text(); })
    .then(function (t) {
      document.getElementById('content').innerHTML = t;
      document.getElementById('ts').textContent =
        new Date().toLocaleTimeString();
    })
    .catch(function () {});
}
document.getElementById('ts').textContent = new Date().toLocaleTimeString();
setInterval(dashRefresh, __INTERVAL_MS__);
</script>
"""


def render_page(body: str, interval_s: float) -> str:
    script = _SCRIPT.replace("__INTERVAL_MS__", str(int(interval_s * 1000)))
    return "\n".join([
        "<!DOCTYPE html>", '<html lang="zh"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Polymarket 实时仪表盘</title>",
        f"<style>{_CSS}{_EXTRA_CSS}</style></head><body><main>",
        "<h1>Polymarket 纸面校准 · 实时仪表盘</h1>",
        '<div class="livebar"><span class="dot"></span>'
        f"实时更新中 · 每 {interval_s:g}s 刷新一次 · "
        '上次刷新 <span id="ts"></span></div>',
        f'<div id="content">{body}</div>',
        "</main>", script, "</body></html>",
    ])


def _make_handler(cfg: Config, window: int, interval_s: float):
    class Handler(BaseHTTPRequestHandler):
        def _send_html(self, body: str, status: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path not in ("/", "/fragment"):
                self._send_html("<h1>404 Not Found</h1>", status=404)
                return
            store = Store(cfg.db_path)
            try:
                body = render_body(store, cfg.bankroll.initial_usdc, window)
            finally:
                store.close()
            if self.path == "/fragment":
                self._send_html(body)
            else:
                self._send_html(render_page(body, interval_s))

        def log_message(self, *args) -> None:
            pass  # 静默:轮询请求不刷屏

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="本地实时仪表盘(只监听 127.0.0.1,不对外网暴露)")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=8.0,
                        help="浏览器刷新间隔(秒,默认 8)")
    parser.add_argument("--window", type=int, default=200,
                        help="滚动 Brier 窗口(默认 200)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    handler = _make_handler(cfg, args.window, args.interval)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"仪表盘: http://127.0.0.1:{args.port} (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
