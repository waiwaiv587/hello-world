"""迷你 WebSocket 实现(RFC 6455,纯标准库)。

只覆盖本项目需要的子集:文本消息收发、ping/pong 自动应答、优雅关闭、
ws:// 与 wss://。不支持 permessage-deflate 压缩(握手时不请求即可)。
客户端供行情订阅用;服务端只为离线测试与本地仿真。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import urllib.parse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 帧操作码
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 8, 9, 10


class ConnectionClosed(Exception):
    pass


def accept_key(key: str) -> str:
    """由握手 Sec-WebSocket-Key 计算 Sec-WebSocket-Accept。"""
    digest = hashlib.sha1((key + _GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def encode_frame(opcode: int, payload: bytes, mask: bool, fin: bool = True) -> bytes:
    b0 = (0x80 if fin else 0x00) | opcode
    mb = 0x80 if mask else 0x00
    n = len(payload)
    if n < 126:
        header = bytes([b0, mb | n])
    elif n < 1 << 16:
        header = bytes([b0, mb | 126]) + n.to_bytes(2, "big")
    else:
        header = bytes([b0, mb | 127]) + n.to_bytes(8, "big")
    if mask:
        key = os.urandom(4)
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return header + key + payload
    return header + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[bool, int, bytes]:
    """读一帧,返回 (fin, opcode, payload);连接断开抛 ConnectionClosed。"""
    try:
        b0, b1 = await reader.readexactly(2)
        n = b1 & 0x7F
        if n == 126:
            n = int.from_bytes(await reader.readexactly(2), "big")
        elif n == 127:
            n = int.from_bytes(await reader.readexactly(8), "big")
        key = await reader.readexactly(4) if b1 & 0x80 else None
        payload = await reader.readexactly(n) if n else b""
    except (asyncio.IncompleteReadError, ConnectionError) as exc:
        raise ConnectionClosed(str(exc)) from exc
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bool(b0 & 0x80), b0 & 0x0F, payload


class WebSocket:
    """握手完成后的双向连接。async for 逐条产出文本消息。"""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 mask_outgoing: bool):
        self._reader = reader
        self._writer = writer
        self._mask = mask_outgoing
        self._wlock = asyncio.Lock()
        self._closed = False
        self._ping_task: asyncio.Task | None = None

    async def _write_frame(self, opcode: int, payload: bytes) -> None:
        async with self._wlock:
            if self._closed:
                raise ConnectionClosed("已关闭")
            self._writer.write(encode_frame(opcode, payload, self._mask))
            await self._writer.drain()

    async def send(self, text: str) -> None:
        await self._write_frame(OP_TEXT, text.encode())

    async def recv(self) -> str:
        """收一条完整文本消息;自动应答 ping;对端关闭抛 ConnectionClosed。"""
        buf = b""
        while True:
            fin, opcode, payload = await read_frame(self._reader)
            if opcode == OP_PING:
                await self._write_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                try:
                    await self._write_frame(OP_CLOSE, payload)
                except (ConnectionClosed, ConnectionError):
                    pass
                await self.close()
                raise ConnectionClosed("对端关闭")
            if opcode in (OP_TEXT, OP_BINARY, OP_CONT):
                buf += payload
                if fin:
                    return buf.decode("utf-8", errors="replace")

    def start_keepalive(self, interval_s: float) -> None:
        """定期发协议层 ping,防止服务端按空闲断开。"""
        async def _loop():
            while not self._closed:
                await asyncio.sleep(interval_s)
                try:
                    await self._write_frame(OP_PING, b"")
                except (ConnectionClosed, ConnectionError):
                    return
        self._ping_task = asyncio.ensure_future(_loop())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ping_task:
            self._ping_task.cancel()
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return await self.recv()
        except ConnectionClosed:
            raise StopAsyncIteration


class _Connect:
    """async with connect(url) as ws: / 或 ws = await connect(url)。"""

    def __init__(self, url: str, ping_interval_s: float | None = None):
        self.url = url
        self.ping_interval_s = ping_interval_s
        self._ws: WebSocket | None = None

    async def _open(self) -> WebSocket:
        u = urllib.parse.urlsplit(self.url)
        if u.scheme not in ("ws", "wss"):
            raise ValueError(f"不支持的协议: {u.scheme}")
        tls = u.scheme == "wss"
        port = u.port or (443 if tls else 80)
        ctx = ssl.create_default_context() if tls else None
        reader, writer = await asyncio.open_connection(
            u.hostname, port, ssl=ctx, server_hostname=u.hostname if tls else None)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        host = u.hostname if port in (80, 443) else f"{u.hostname}:{port}"
        writer.write(
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: polymarket-paper/0.1\r\n\r\n".encode())
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            writer.close()
            raise ConnectionClosed(f"握手失败: {status.decode(errors='replace')}")
        expected = accept_key(key).encode()
        if expected not in head:
            writer.close()
            raise ConnectionClosed("握手校验失败: Sec-WebSocket-Accept 不匹配")
        ws = WebSocket(reader, writer, mask_outgoing=True)
        if self.ping_interval_s:
            ws.start_keepalive(self.ping_interval_s)
        return ws

    def __await__(self):
        return self._open().__await__()

    async def __aenter__(self) -> WebSocket:
        self._ws = await self._open()
        return self._ws

    async def __aexit__(self, *exc) -> None:
        if self._ws:
            await self._ws.close()


def connect(url: str, ping_interval_s: float | None = None) -> _Connect:
    return _Connect(url, ping_interval_s)


# ---- 服务端(仅测试与本地仿真用) ----

async def server_handshake(reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> WebSocket:
    head = await reader.readuntil(b"\r\n\r\n")
    key = ""
    for line in head.decode(errors="replace").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    if not key:
        writer.close()
        raise ConnectionClosed("缺少 Sec-WebSocket-Key")
    writer.write(
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n".encode())
    await writer.drain()
    return WebSocket(reader, writer, mask_outgoing=False)


async def start_ws_server(handler, host: str = "127.0.0.1", port: int = 0):
    """启动测试用 WS 服务;handler(ws) 为协程。返回 asyncio.Server。"""
    async def _on_conn(reader, writer):
        try:
            ws = await server_handshake(reader, writer)
        except ConnectionClosed:
            return
        try:
            await handler(ws)
        except (ConnectionClosed, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            await ws.close()
    return await asyncio.start_server(_on_conn, host, port)
