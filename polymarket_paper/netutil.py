"""HTTP JSON 工具(纯标准库):urllib 放到线程池里跑,包成 async 接口。"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request


def _get_sync(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "User-Agent": "polymarket-paper/0.1",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def get_json(url: str, params: dict | None = None, timeout: float = 10.0):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    return await asyncio.to_thread(_get_sync, url, timeout)
