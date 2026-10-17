"""Metadata caches: describe reseeds the schema, region labels are fetched once."""

from __future__ import annotations

import pytest

from seattle_open_data.core import cache
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
    assert _views_fetched(soda) == 2


@pytest.mark.soda_fixture("describe_fire_full")
async def test_region_labels_are_fetched_once_per_feature(client, soda):
    args = {"dataset_id": FIRE, "detail": "full", "column_query": "type"}
    first = wire(await client.call_tool_mcp("seattle_data_describe_dataset", args))
    layer_reads = [r for r in soda.requests if "_feature_id" in r]
    assert layer_reads, "the first describe resolves labels upstream"

    soda.requests.clear()
    second = wire(await client.call_tool_mcp("seattle_data_describe_dataset", args))
    assert not [r for r in soda.requests if "_feature_id" in r]
    assert second["structuredContent"]["place_fields"] == first["structuredContent"]["place_fields"]


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
