import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from polymarket_paper.netutil import get_json


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlsplit(self.path)
        body = json.dumps({"path": u.path,
                           "params": parse_qs(u.query)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestGetJson(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()

    async def test_get_with_params(self):
        data = await get_json(f"{self.base}/markets",
                              params={"closed": "false", "limit": 300})
        self.assertEqual(data["path"], "/markets")
        self.assertEqual(data["params"], {"closed": ["false"], "limit": ["300"]})

    async def test_get_without_params(self):
        data = await get_json(f"{self.base}/ok")
        self.assertEqual(data["path"], "/ok")


if __name__ == "__main__":
    unittest.main()
