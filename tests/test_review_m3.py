"""Regressions for the Codex review of milestone 3."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from seattle_open_data.constants import RESULT_BYTE_BUDGET
from seattle_open_data.core import cache, query
from seattle_open_data.core.http import SodaClient, set_client
from tests.conftest import wire

COLUMNS: list[dict[str, Any]] = [
    {"fieldName": "name", "dataTypeName": "text"},
    {"fieldName": "report_location", "dataTypeName": "point"},
]
VIEW = {"id": "aaaa-bbbb", "assetType": "dataset", "viewType": "tabular", "columns": COLUMNS}


def install(rows, fields=("name", "report_location"), types=("text", "point")):
    """A fake portal that returns `rows` (as raw JSON text) for every resource query."""
    sent = []

    def handler(request):
        if "/api/views/" in request.url.path:
            return httpx.Response(200, json=VIEW)
        sent.append(dict(request.url.params.multi_items()))
        headers = {
            "X-SODA2-Fields": json.dumps(list(fields)),
            "X-SODA2-Types": json.dumps(list(types)),
        }
        return httpx.Response(200, headers=headers, content=rows.encode())

    cache.clear()
    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    return sent


async def call(client, args):
    return wire(await client.call_tool_mcp("seattle_data_query_dataset", args))


async def test_point_coordinates_serialize(client):
    install(
        '[{"name":"a","report_location":{"type":"Point","coordinates":[-122.33,47.6]},":id":"row-1"}]'
    )
    result = await call(client, {"dataset_id": "aaaa-bbbb", "select": ["name", "report_location"]})
    assert result["isError"] is False, result["structuredContent"]
    assert result["structuredContent"]["rows"][0]["report_location"]["coordinates"] == [
        -122.33,
        47.6,
    ]


async def test_long_clause_cursor_counts_against_budget(client):
    row = {"name": "x" * 300, ":id": "row-{}"}
    install("[" + ",".join(json.dumps({**row, ":id": f"row-{i}"}) for i in range(300)) + "]")
    where = "name != '" + "y" * 3900 + "'"
    result = await call(
        client, {"dataset_id": "aaaa-bbbb", "select": ["name"], "where": where, "page_size": 200}
    )
    out = result["structuredContent"]
    assert out["has_more"] is True and len(out["next_cursor"]) > 4000
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= RESULT_BYTE_BUDGET


async def test_escape_heavy_rows_count_against_budget(client):
    row = json.dumps({"name": '"' * 5000, ":id": "row-1"})
    install("[" + ",".join([row] * 5) + "]")
    result = await call(client, {"dataset_id": "aaaa-bbbb", "select": ["name"], "page_size": 5})
    if result["isError"]:
        assert result["structuredContent"]["code"] == "row_too_large"
    else:
        assert len(json.dumps(result, separators=(",", ":")).encode()) <= RESULT_BYTE_BUDGET
        assert result["structuredContent"]["returned_rows"] < 5


def plan(select, order_by=None):
    return query._plan("aaaa-bbbb", {"select": select, "order_by": order_by}, COLUMNS, [])


def test_distinct_orders_by_every_selected_key():
    assert plan(["DISTINCT name"])["params"]["$order"] == "name"
    two = plan(["DISTINCT name", "date_trunc_y(dt) AS year"], order_by=["year DESC"])
    assert two["params"]["$order"] == "year DESC, name"
    assert two["kind"] == "offset"


def test_distinct_orders_by_a_backtick_alias_not_its_declaration():
    assert plan(["DISTINCT name AS `area`"])["params"]["$order"] == "`area`"
    assert plan(["DISTINCT name AS `area`"], order_by=["area DESC"])["params"]["$order"] == (
        "area DESC"
    )
    assert plan(["DISTINCT name AS `area`"], order_by=["`area`"])["params"]["$order"] == "`area`"


@pytest.mark.parametrize("value", [1, 2.5, -122.33])
def test_plain_numbers(value):
    from decimal import Decimal

    assert query._plain(Decimal(str(value))) == value
