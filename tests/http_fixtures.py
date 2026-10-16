"""Record real SODA responses once, replay them in tests.

`RECORD_FIXTURES=1 uv run pytest` hits the live API and rewrites fixture files;
a plain run replays them and fails on any request not recorded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "http"
RECORDING = bool(os.environ.get("RECORD_FIXTURES"))


def _key(request: httpx.Request) -> str:
    params = sorted(request.url.params.multi_items())
    return request.url.host + request.url.path + "?" + json.dumps(params)


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self, name: str) -> None:
        self.path = FIXTURES / f"{name}.json"
        self.entries: dict[str, dict[str, Any]] = {}
        self._live = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._live.handle_async_request(request)
        body = await response.aread()
        self.entries[_key(request)] = {
            "status": response.status_code,
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k.lower() in ("content-type", "retry-after")
            },
            "body": json.loads(body) if body else None,
        }
        # `aread()` already decoded the body; forwarding Content-Encoding would decode it twice.
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-encoding", "content-length")
        }
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    def save(self) -> None:
        self.path.write_text(json.dumps(self.entries, indent=1, sort_keys=True) + "\n")


class Replay(httpx.AsyncBaseTransport):
    def __init__(self, name: str, overrides: dict[str, Any] | None = None) -> None:
        self.entries: dict[str, dict[str, Any]] = json.loads(
            (FIXTURES / f"{name}.json").read_text()
        )
        self.overrides = overrides or {}
        self.requests: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = _key(request)
        self.requests.append(key)
        for fragment, action in self.overrides.items():
            if fragment in key:
                if action == "timeout":
                    raise httpx.ReadTimeout("simulated", request=request)
                status, body = action
                return httpx.Response(status, json=body, request=request)
        if key not in self.entries:
            raise AssertionError(f"unrecorded request: {key}")
        entry = self.entries[key]
        return httpx.Response(
            entry["status"], headers=entry["headers"], json=entry["body"], request=request
        )
