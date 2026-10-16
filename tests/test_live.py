"""Live checks of the upstream facts in docs/upstream-facts.md. Run with --run-integration."""

from __future__ import annotations

import pytest

from seattle_open_data.core.guides import GUIDES
from seattle_open_data.core.http import SodaClient, set_client
from tests.conftest import wire

pytestmark = pytest.mark.integration


@pytest.fixture
def live(no_network):
    set_client(SodaClient())
    yield
    set_client(None)


async def test_catalog_alias_still_required(live):
    client = SodaClient()
    url = "https://api.us.socrata.com/api/catalog/v1"
    bare = await client.get_json(url, {"domains": "data.seattle.gov", "limit": 1})
    alias = await client.get_json(url, {"domains": "cos-data.seattle.gov", "limit": 1})
    assert bare["resultSetSize"] == 0
    assert alias["resultSetSize"] > 100


async def test_search_and_describe_fire_911(live, client):
    found = wire(await client.call_tool_mcp("seattle_data_search_datasets", {"query": "fire 911"}))
    assert any(d["dataset_id"] == "kzjm-xkqj" for d in found["structuredContent"]["datasets"])
    described = wire(
        await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"})
    )["structuredContent"]
    assert described["row_count"]["value"] > 2_000_000
    assert any(p["column"] == ":@computed_region_ru88_fbhk" for p in described["place_fields"])


async def test_region_layer_label_columns(live):
    client = SodaClient()
    for layer, label in {
        "ru88-fbhk": "name",
        "cyqu-gs94": "c_district",
        "kuhn-3gp2": "name",
        "q256-3sug": "zcta5ce10",
        "2day-rhn5": "name10",
    }.items():
        view = await client.view(layer)
        assert label in {c["fieldName"] for c in view["columns"]}, layer


@pytest.mark.parametrize(
    "example",
    [e for g in GUIDES for e in g.examples],
    ids=lambda e: e.question[:40],
)
async def test_guide_examples_run(live, client, example):
    result = wire(await client.call_tool_mcp("seattle_data_query_dataset", example.arguments))
    assert result["isError"] is False, result["structuredContent"]
    assert result["structuredContent"]["returned_rows"] > 0
