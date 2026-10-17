"""Regressions for the Codex whole-project review, plus a per-tool fault matrix."""

from __future__ import annotations

import json
from typing import Any

import anyio
import httpx
import pytest

from seattle_open_data.constants import INSTRUCTIONS_SENTINEL, RESULT_BYTE_BUDGET
from seattle_open_data.core import cache, query
from seattle_open_data.core.errors import ErrorCode, SeattleDataError
from seattle_open_data.core.http import SodaClient, set_client
from seattle_open_data.core.reference import ERROR_CODES_BY_TOOL, INSTRUCTIONS
from tests.conftest import wire

VIEW = {
    "id": "aaaa-bbbb",
    "assetType": "dataset",
    "viewType": "tabular",
    "columns": [
        {"fieldName": "name", "dataTypeName": "text"},
        {"fieldName": "when", "dataTypeName": "floating_timestamp"},
    ],
}
CATALOG = {"results": [], "resultSetSize": "0"}


def portal(
    resource: Any = None,
    *,
    view: Any = VIEW,
    catalog: Any = CATALOG,
    status: int = 200,
    headers=None,
):
    """A fake portal; `resource` is the body (or a callable returning a Response) for /resource/."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/views/" in request.url.path:
            return httpx.Response(200, json=view)
        if "/catalog/" in request.url.path:
            return httpx.Response(status, json=catalog, headers=headers or {})
        if callable(resource):
            return resource(request)
        content = resource if isinstance(resource, bytes) else json.dumps(resource).encode()
        return httpx.Response(
            status, content=content, headers={"X-SODA2-Fields": '["name"]', **(headers or {})}
        )

    cache.clear()
    set_client(SodaClient(transport=httpx.MockTransport(handler)))


async def call(client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return wire(await client.call_tool_mcp(tool, args))


# --- 1. unexpected shapes are errors, not empty successes ---------------------


@pytest.mark.parametrize(
    "body", [None, {"error": True, "message": "upstream failure"}, {"rows": []}, [1, 2]]
)
async def test_query_rejects_non_row_bodies(client, body):
    portal(body)
    result = await call(
        client, "seattle_data_query_dataset", {"dataset_id": "aaaa-bbbb", "select": ["name"]}
    )
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "upstream_unavailable"


async def test_query_empty_array_is_a_real_empty_result(client):
    portal([])
    result = await call(
        client, "seattle_data_query_dataset", {"dataset_id": "aaaa-bbbb", "select": ["name"]}
    )
    assert result["isError"] is False
    assert result["structuredContent"]["returned_rows"] == 0


@pytest.mark.parametrize("body", [None, {"error": True}, {"results": "nope", "resultSetSize": 0}])
async def test_search_rejects_malformed_catalog(client, body):
    portal(catalog=body)
    result = await call(client, "seattle_data_search_datasets", {"query": "fire"})
    assert result["structuredContent"]["code"] == "upstream_unavailable"


async def test_describe_rejects_malformed_count(client):
    portal({"error": True})
    result = await call(client, "seattle_data_describe_dataset", {"dataset_id": "aaaa-bbbb"})
    assert result["isError"] is False, "a failed aggregate degrades the field, never the call"
    assert result["structuredContent"]["row_count"]["status"] == "unavailable"


# --- 2. overall deadline bounds a trickling response --------------------------


async def test_trickling_stream_hits_the_whole_call_deadline(client, monkeypatch):
    async def trickle():
        yield b"["
        for _ in range(50):
            await anyio.sleep(0.02)
            yield b" "
        yield b"]"

    portal(lambda request: httpx.Response(200, content=trickle()))
    monkeypatch.setattr(query, "QUERY_DEADLINE_SECONDS", 0.2)
    result = await call(
        client, "seattle_data_query_dataset", {"dataset_id": "aaaa-bbbb", "select": ["name"]}
    )
    assert result["structuredContent"]["code"] == "query_timeout"
    assert result["structuredContent"]["repair"]["next_step"] == "inspect_for_narrowing"


async def test_whole_call_deadline_in_the_metadata_fetch_has_no_repair(client, monkeypatch):
    async def slow_view(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(2)  # the whole-call deadline expires first, inside the fetch
        return httpx.Response(200, json=VIEW)

    cache.clear()
    set_client(SodaClient(transport=httpx.MockTransport(slow_view)))
    monkeypatch.setattr(query, "QUERY_DEADLINE_SECONDS", 0.2)
    result = await call(
        client, "seattle_data_query_dataset", {"dataset_id": "aaaa-bbbb", "select": ["name"]}
    )
    envelope = result["structuredContent"]
    assert envelope["code"] == "query_timeout"
    assert "repair" not in envelope, "describe would wait on the same metadata"
    assert envelope["details"]["reason"] == "The dataset's metadata did not load."


async def test_get_json_deadline_is_total_not_per_read():
    async def trickle():
        for _ in range(50):
            await anyio.sleep(0.02)
            yield b" "
        yield b"[]"

    client = SodaClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=trickle()))
    )
    with pytest.raises(SeattleDataError) as caught:
        await client.get_json("https://data.seattle.gov/api/views/x.json", timeout=0.2)
    assert caught.value.envelope.code == ErrorCode.QUERY_TIMEOUT


# --- 3. an empty page cannot exceed the budget --------------------------------


async def test_wide_schema_with_no_rows_stays_in_budget(client):
    columns = [
        {"fieldName": f"column_{i:03d}_" + "x" * 150, "dataTypeName": "text"} for i in range(200)
    ]
    fields = [c["fieldName"] for c in columns]

    def empty(request):
        return httpx.Response(
            200,
            content=b"[]",
            headers={
                "X-SODA2-Fields": json.dumps(fields),
                "X-SODA2-Types": json.dumps(["text"] * 200),
            },
        )

    portal(empty, view={**VIEW, "columns": columns})
    result = await call(client, "seattle_data_query_dataset", {"dataset_id": "aaaa-bbbb"})
    if result["isError"]:
        assert result["structuredContent"]["code"] == "row_too_large"
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= RESULT_BYTE_BUDGET


# --- 4. region-label failures are visible --------------------------------------

REGION_VIEW = {
    "id": "aaaa-bbbb",
    "assetType": "dataset",
    "viewType": "tabular",
    "columns": [
        {
            "fieldName": ":@computed_region_ru88_fbhk",
            "dataTypeName": "number",
            "computationStrategy": {"parameters": {"region": "_ru88-fbhk"}},
        },
    ],
}


def region_portal(label_status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/views/" in request.url.path:
            return httpx.Response(200, json=REGION_VIEW)
        if "ru88-fbhk.json" in request.url.path:
            return httpx.Response(
                label_status, json={"message": "nope"}, headers={"Retry-After": "12"}
            )
        select = request.url.params.get("$select", "")
        if "count(*) AS n" in select and "AS v" not in select:
            return httpx.Response(200, json=[{"n": "10"}])
        return httpx.Response(200, json=[{"v": "8", "n": "10"}])

    set_client(SodaClient(transport=httpx.MockTransport(handler)))


async def test_label_rate_limit_is_reported(client):
    region_portal(429)
    result = await call(
        client, "seattle_data_describe_dataset", {"dataset_id": "aaaa-bbbb", "detail": "full"}
    )
    assert result["structuredContent"]["code"] == "rate_limited"
    assert result["structuredContent"]["retry_after_ms"] == 12_000


async def test_label_outage_degrades_visibly(client):
    region_portal(503)
    result = await call(
        client, "seattle_data_describe_dataset", {"dataset_id": "aaaa-bbbb", "detail": "full"}
    )
    field = result["structuredContent"]["place_fields"][0]
    assert field["labels_available"] is False
    assert field["values"] == [{"label": "8", "value": "8"}]


# --- 5. describe samples are sanitized like query rows -------------------------


async def test_sample_rows_strip_control_characters(client):
    def handler(request):
        if "/api/views/" in request.url.path:
            return httpx.Response(200, json=VIEW)
        if "$limit" in request.url.params:
            return httpx.Response(200, json=[{"name": "a\x1bb\x07c", "when": None}])
        return httpx.Response(200, json=[{"n": "1", "lo": None, "hi": None}])

    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    result = await call(
        client, "seattle_data_describe_dataset", {"dataset_id": "aaaa-bbbb", "sample_rows": 1}
    )
    assert result["structuredContent"]["sample_rows"] == [{"name": "abc", "when": None}]


# --- 6. every emitted code is documented for its tool --------------------------

FAULTS: list[tuple[int, Any, dict[str, str]]] = [
    (400, {"code": "query.soql.no-such-column"}, {}),
    (400, {"code": "query.soql.type-mismatch"}, {}),
    (403, {"code": "permission_denied"}, {}),
    (404, {"code": "not_found"}, {}),
    (429, {}, {"Retry-After": "3"}),
    (500, {}, {}),
    (200, {"error": True}, {}),
]
CALLS = {
    "seattle_data_search_datasets": {"query": "fire"},
    "seattle_data_describe_dataset": {"dataset_id": "aaaa-bbbb", "detail": "full"},
    "seattle_data_query_dataset": {"dataset_id": "aaaa-bbbb", "select": ["name"]},
}


@pytest.mark.parametrize("tool", list(CALLS))
@pytest.mark.parametrize(("status", "body", "headers"), FAULTS)
async def test_emitted_codes_are_documented(client, tool, status, body, headers):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/views/" in request.url.path and tool != "seattle_data_describe_dataset":
            return httpx.Response(200, json=VIEW)
        return httpx.Response(status, json=body, headers=headers)

    cache.clear()
    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    result = await call(client, tool, CALLS[tool])
    if result["isError"]:
        assert result["structuredContent"]["code"] in ERROR_CODES_BY_TOOL[tool]


async def test_catalog_404_is_not_a_missing_dataset(client):
    portal(status=404, catalog={"code": "not_found"})
    envelope = (await call(client, "seattle_data_search_datasets", {"query": "fire"}))[
        "structuredContent"
    ]
    assert envelope["code"] == "upstream_unavailable"


# --- 7. the instructions head says how to detect truncation --------------------


def test_instructions_head_names_the_sentinel_and_recovery():
    head = "\n".join(INSTRUCTIONS.splitlines()[:2])
    assert INSTRUCTIONS_SENTINEL in head
    assert 'seattle_data_get_reference(topic="capabilities")' in head
    assert INSTRUCTIONS.splitlines()[-1] == INSTRUCTIONS_SENTINEL
