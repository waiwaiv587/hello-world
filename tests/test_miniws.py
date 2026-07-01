import asyncio
import unittest

from polymarket_paper import miniws
from polymarket_paper.miniws import (
    OP_PING, OP_TEXT, accept_key, encode_frame, read_frame)


class TestFrames(unittest.IsolatedAsyncioTestCase):
    async def _roundtrip(self, data: bytes, mask: bool):
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(OP_TEXT, data, mask=mask))
        fin, opcode, payload = await read_frame(reader)
        self.assertTrue(fin)
        self.assertEqual(opcode, OP_TEXT)
        self.assertEqual(payload, data)

    async def test_roundtrip_masked_and_unmasked(self):
        for mask in (True, False):
            await self._roundtrip("你好 world".encode(), mask)

    async def test_length_encodings(self):
        # 7 位、16 位、64 位三种长度编码
        for n in (10, 300, 70_000):
            await self._roundtrip(b"x" * n, mask=True)

    async def test_ping_frame(self):
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(OP_PING, b"hb", mask=False))
        _, opcode, payload = await read_frame(reader)
        self.assertEqual(opcode, OP_PING)
        self.assertEqual(payload, b"hb")


class TestHandshake(unittest.TestCase):
    def test_rfc6455_accept_vector(self):
        # RFC 6455 §1.3 官方测试向量
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class TestClientServer(unittest.IsolatedAsyncioTestCase):
    async def test_echo_roundtrip(self):
        async def handler(ws):
            async for msg in ws:
                await ws.send(f"echo:{msg}")

        server = await miniws.start_ws_server(handler)
        port = server.sockets[0].getsockname()[1]
        try:
            async with miniws.connect(f"ws://127.0.0.1:{port}/x") as ws:
                await ws.send("行情1")
                self.assertEqual(await ws.recv(), "echo:行情1")
                await ws.send("行情2")
                self.assertEqual(await ws.recv(), "echo:行情2")
        finally:
            server.close()
            await server.wait_closed()

    async def test_server_ping_is_answered_transparently(self):
        async def handler(ws):
            await ws._write_frame(OP_PING, b"keepalive")
            await ws.send("after-ping")
            # 客户端应回 pong
            _, opcode, payload = await read_frame(ws._reader)
            assert opcode == miniws.OP_PONG and payload == b"keepalive"
            await ws.send("pong-ok")

        server = await miniws.start_ws_server(handler)
        port = server.sockets[0].getsockname()[1]
        try:
            async with miniws.connect(f"ws://127.0.0.1:{port}/") as ws:
                self.assertEqual(await ws.recv(), "after-ping")
                self.assertEqual(await ws.recv(), "pong-ok")
        finally:
            server.close()
            await server.wait_closed()

    async def test_iteration_ends_on_server_close(self):
        async def handler(ws):
            await ws.send("only-one")

        server = await miniws.start_ws_server(handler)
        port = server.sockets[0].getsockname()[1]
        try:
            got = []
            async with miniws.connect(f"ws://127.0.0.1:{port}/") as ws:
                async for msg in ws:
                    got.append(msg)
            self.assertEqual(got, ["only-one"])
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
