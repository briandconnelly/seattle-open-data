"""Failure paths of the upstream client and cursors."""

from __future__ import annotations

import httpx
import pytest

from seattle_open_data.constants import UPSTREAM_MAX_BYTES
from seattle_open_data.core import cursor
from seattle_open_data.core.errors import ErrorCode, Repair, SeattleDataError
from seattle_open_data.core.http import SodaClient

RESTART = Repair(next_step="restart", tool="seattle_data_search_datasets", arguments={})


async def code_of(handler) -> ErrorCode:
    client = SodaClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SeattleDataError) as caught:
        await client.get_json("https://data.seattle.gov/api/views/kzjm-xkqj.json")
    return caught.value.envelope.code


async def test_token_is_sent_as_header_never_as_param(monkeypatch):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok123")
    seen = {}

    def handler(request):
        seen["header"] = request.headers.get("X-App-Token")
        seen["query"] = str(request.url.query)
        return httpx.Response(200, json={})

    client = SodaClient(transport=httpx.MockTransport(handler))
    assert client.token_configured
    await client.get_json("https://data.seattle.gov/resource/x.json", {"$limit": 1})
    assert seen["header"] == "tok123"
    assert "tok123" not in seen["query"]


async def test_connection_failure_is_temporary():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    assert await code_of(handler) == ErrorCode.UPSTREAM_UNAVAILABLE


async def test_non_json_body():
    assert (
        await code_of(lambda r: httpx.Response(200, text="<html>"))
        == ErrorCode.UPSTREAM_UNAVAILABLE
    )


async def test_oversized_response():
    big = b"[" + b"0," * (UPSTREAM_MAX_BYTES // 2) + b"0]"
    assert (
        await code_of(lambda r: httpx.Response(200, content=big)) == ErrorCode.UPSTREAM_UNAVAILABLE
    )


async def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert await code_of(handler) == ErrorCode.QUERY_TIMEOUT


def test_cursor_round_trip():
    token = cursor.encode("search", {"off": 10})
    assert cursor.decode(token, "search", restart=RESTART) == {"off": 10}


def test_cursor_for_another_tool_is_invalid():
    token = cursor.encode("search", {"off": 10})
    with pytest.raises(SeattleDataError) as caught:
        cursor.decode(token, "query", restart=RESTART)
    assert caught.value.envelope.code == ErrorCode.INVALID_CURSOR


def test_cursor_expires(monkeypatch):
    token = cursor.encode("search", {"off": 10})
    real = cursor.time.time
    monkeypatch.setattr(cursor.time, "time", lambda: real() + 3601)
    with pytest.raises(SeattleDataError) as caught:
        cursor.decode(token, "search", restart=RESTART)
    assert caught.value.envelope.code == ErrorCode.CURSOR_EXPIRED
    assert caught.value.envelope.repair == RESTART


async def test_stream_rows_reads_pretty_printed_json():
    """Filter views answer with pretty-printed JSON, but replayed fixtures are compact."""
    body = (
        b'[ {\n  ":id" : "row-1",\n  "name" : "a"\n}, {\n  ":id" : "row-2",\n  "name" : "b"\n} ]\n'
    )

    def handler(request):
        return httpx.Response(200, headers={"X-SODA2-Fields": '[":id","name"]'}, content=body)

    client = SodaClient(transport=httpx.MockTransport(handler))
    out = await client.stream_rows("8tqq-u7ib", {}, max_rows=1)
    assert out.rows == [{":id": "row-1", "name": "a"}]
    assert out.more is True
    assert out.fields == [":id", "name"]
