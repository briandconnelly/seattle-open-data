"""Regressions for the Codex review of milestone 2."""

from __future__ import annotations

import gc
import warnings
from typing import Any, cast

import anyio
import httpx
import pytest

from seattle_open_data.constants import UPSTREAM_MAX_BYTES
from seattle_open_data.core import catalog, describe
from seattle_open_data.core.errors import ErrorCode, SeattleDataError
from seattle_open_data.core.http import SodaClient
from tests.conftest import wire


async def test_redirect_is_refused_and_token_never_follows(monkeypatch):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok123")
    hosts = []

    def handler(request):
        hosts.append((request.url.host, request.headers.get("X-App-Token")))
        return httpx.Response(302, headers={"Location": "http://untrusted.invalid/steal"})

    client = SodaClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SeattleDataError) as caught:
        await client.get_json("https://data.seattle.gov/api/views/kzjm-xkqj.json")
    assert caught.value.envelope.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert hosts == [("data.seattle.gov", "tok123")]


async def test_size_cap_stops_reading_early():
    chunk = b"x" * (1024 * 1024)
    served = 0

    async def body():
        nonlocal served
        for _ in range(32):
            served += 1
            yield chunk

    def handler(request):
        return httpx.Response(200, content=body())

    client = SodaClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SeattleDataError):
        await client.get_json("https://data.seattle.gov/api/views/x.json")
    assert served <= UPSTREAM_MAX_BYTES // len(chunk) + 1


@pytest.mark.soda_fixture(
    "describe_fire_summary", overrides={"count(*)": (429, {"message": "slow down"})}
)
async def test_rate_limit_during_describe_is_reported(client, soda):
    result = wire(
        await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"})
    )
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "rate_limited"


@pytest.mark.soda_fixture(
    "describe_fire_summary", overrides={"count(*)": (403, {"code": "permission_denied"})}
)
async def test_forbidden_during_describe_is_reported(client, soda):
    result = wire(
        await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"})
    )
    assert result["structuredContent"]["code"] == "upstream_forbidden"


@pytest.mark.soda_fixture("describe_fire_full", overrides={"AS v": (503, {"message": "down"})})
async def test_failed_place_stats_are_unavailable_not_timeout(client, soda):
    args = {"dataset_id": "kzjm-xkqj", "detail": "full", "column_query": "zzz"}
    out = wire(await client.call_tool_mcp("seattle_data_describe_dataset", args))[
        "structuredContent"
    ]
    assert {p["stats_status"] for p in out["place_fields"]} == {"unavailable"}


def test_assigned_fraction_uses_one_snapshot():
    groups = describe._Outcome()
    groups.status, groups.value = "ok", [{"n": "150"}, {"v": "1", "n": "50"}]
    spec = {"column": ":@c", "kind": "zip", "source": "computed_region", "reliability": "ok"}
    field = describe._place_field(spec, groups, {})
    assert field.assigned_fraction == 0.25


@pytest.mark.soda_fixture("describe_fire_summary", overrides={"min(datetime)": "timeout"})
async def test_coverage_timeout_falls_back_to_cache(client, soda):
    out = wire(
        await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"})
    )["structuredContent"]
    coverage = out["data_coverage"][0]
    assert coverage["status"] == "timeout"
    assert coverage["origin"] == "cached_metadata"
    assert coverage["min"] and coverage["max"]


async def test_map_match_folds_into_parent_not_on_page():
    def handler(request):
        params = dict(request.url.params.multi_items())
        if "ids" in params:
            item = {
                "resource": {"id": "76t5-zqzr", "name": "Building Permits", "type": "dataset"},
                "permalink": "https://data.seattle.gov/d/76t5-zqzr",
            }
            return httpx.Response(200, json={"results": [item], "resultSetSize": 1})
        view = {
            "resource": {
                "id": "5rc4-5s78",
                "name": "Permits map",
                "type": "map",
                "parent_fxf": ["76t5-zqzr"],
            }
        }
        return httpx.Response(200, json={"results": [view], "resultSetSize": 1})

    client = SodaClient(transport=httpx.MockTransport(handler))
    out = await catalog.search(
        client,
        query="permits map",
        asset_scope="tabular",
        category=None,
        page_size=10,
        cursor=None,
        detail="summary",
    )
    assert [d.dataset_id for d in out.datasets] == ["76t5-zqzr"]


async def test_deadline_does_not_strand_queued_coroutines(monkeypatch):
    columns = [
        {
            "fieldName": f":@computed_region_l{i}",
            "dataTypeName": "number",
            "computationStrategy": {"parameters": {"region": f"_aaaa-bbb{i}"}},
        }
        for i in range(6)
    ]

    class Slow:
        async def view(self, dataset_id):
            return {
                "id": dataset_id,
                "assetType": "dataset",
                "viewType": "tabular",
                "columns": columns,
            }

        async def soql(self, dataset_id, params, timeout=None):
            await anyio.sleep(1)
            return []

    monkeypatch.setattr(describe, "DESCRIBE_AGGREGATE_TIMEOUT_SECONDS", 0.05)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await describe.describe(
            cast("Any", Slow()),
            dataset_id="aaaa-bbbb",
            column_query=None,
            sample_rows=0,
            detail="full",
        )
        gc.collect()
    assert not [w for w in caught if "never awaited" in str(w.message)]
