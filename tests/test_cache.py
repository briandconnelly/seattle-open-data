"""Metadata caches: describe reseeds the schema, region labels are fetched once."""

from __future__ import annotations

import anyio
import pytest

from seattle_open_data.core import cache, describe
from seattle_open_data.core.cache import TtlCache
from seattle_open_data.core.http import SodaClient, set_client
from tests.conftest import wire
from tests.http_fixtures import Replay

FIRE = "kzjm-xkqj"
THREE_CALLS = "incident_number in ('F260124133', 'F260124199', 'F260124257')"


def _views_fetched(transport: Replay) -> int:
    return sum("/api/views/" in r for r in transport.requests)


@pytest.mark.soda_fixture("describe_fire_summary")
async def test_describe_then_query_fetches_the_schema_once(client, soda):
    described = wire(
        await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": FIRE})
    )
    assert described["isError"] is False
    assert FIRE in cache.views

    replay = Replay("query_three_rows")
    set_client(SodaClient(transport=replay))
    queried = wire(
        await client.call_tool_mcp(
            "seattle_data_query_dataset",
            {
                "dataset_id": FIRE,
                "select": ["incident_number"],
                "where": THREE_CALLS,
                "page_size": 3,
            },
        )
    )
    assert queried["isError"] is False
    assert _views_fetched(replay) == 0, "query reuses the schema describe just fetched"


@pytest.mark.soda_fixture("describe_fire_summary")
async def test_describe_always_fetches_a_fresh_schema(client, soda):
    for _ in range(2):
        result = await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": FIRE})
        assert wire(result)["isError"] is False
    if hasattr(soda, "requests"):
        assert _views_fetched(soda) == 2


@pytest.mark.soda_fixture("describe_fire_full")
async def test_region_labels_are_fetched_once_per_feature(client, soda):
    args = {"dataset_id": FIRE, "detail": "full", "column_query": "type"}
    first = wire(await client.call_tool_mcp("seattle_data_describe_dataset", args))
    if not hasattr(soda, "requests"):
        return  # recording: the second call would only repeat recorded requests
    assert [r for r in soda.requests if "_feature_id" in r], "the first call resolves labels"

    soda.requests.clear()
    second = wire(await client.call_tool_mcp("seattle_data_describe_dataset", args))
    assert not [r for r in soda.requests if "_feature_id" in r]
    assert second["structuredContent"]["place_fields"] == first["structuredContent"]["place_fields"]


#: One feature per region layer on Fire 911, from the describe_fire_full fixture.
_ONE_LABEL_PER_LAYER = {
    "2day-rhn5": "97",
    "cyqu-gs94": "1",
    "kuhn-3gp2": "1",
    "q256-3sug": "4839",
    "ru88-fbhk": "1",
}


def _warm_one_label_per_layer() -> None:
    for layer, feature in _ONE_LABEL_PER_LAYER.items():
        cache.region_labels.put((layer, feature), "CACHED")


def _region_fields(result: dict) -> list[dict]:
    assert result["isError"] is False, result["structuredContent"]
    fields = result["structuredContent"]["place_fields"]
    return [p for p in fields if p["source"] == "computed_region" and p.get("values")]


@pytest.mark.soda_fixture(
    "describe_fire_full", overrides={"_feature_id": (500, {"message": "unavailable"})}
)
async def test_partial_label_cache_does_not_hide_a_failed_lookup(client, soda):
    _warm_one_label_per_layer()
    args = {"dataset_id": FIRE, "detail": "full", "column_query": "type"}
    fields = _region_fields(wire(await client.call_tool_mcp("seattle_data_describe_dataset", args)))
    assert fields
    for field in fields:
        assert field["labels_available"] is False, field["column"]
        assert all(v["label"] == v["value"] for v in field["values"]), "names stay ids"


@pytest.mark.soda_fixture("describe_fire_full")
async def test_partial_label_cache_does_not_hide_a_label_timeout(client, soda, monkeypatch):
    soql = SodaClient.soql

    async def hang_on_labels(self, dataset_id, params, *a, **kw):
        if "_feature_id" in params.get("$select", ""):
            await anyio.sleep(60)
        return await soql(self, dataset_id, params, *a, **kw)

    monkeypatch.setattr(SodaClient, "soql", hang_on_labels)
    monkeypatch.setattr(describe, "_LABEL_FETCH_TIMEOUT", 0.05)
    _warm_one_label_per_layer()
    args = {"dataset_id": FIRE, "detail": "full", "column_query": "type"}
    fields = _region_fields(wire(await client.call_tool_mcp("seattle_data_describe_dataset", args)))
    assert fields
    assert all(f.get("labels_available") is not True for f in fields)


def test_ttl_cache_expires_and_evicts(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    entries: TtlCache[str, int] = TtlCache(ttl=10, max_entries=2)

    entries.put("a", 1)
    entries.put("b", 2)
    entries.put("a", 3)  # rewriting moves "a" behind "b"
    entries.put("c", 4)
    assert "b" not in entries, "the oldest write is evicted"
    assert entries.get("a") == 3 and entries.get("c") == 4

    now[0] = 10.0
    assert entries.get("a") is None, "entries expire at the TTL"
    assert len(entries) == 1
