"""Curated guides: every example is a valid call, and every guide is reachable."""

from __future__ import annotations

import json

import jsonschema
import pytest

from seattle_open_data.core import arguments
from seattle_open_data.core.guides import GUIDES
from tests.conftest import wire

EXAMPLES = [(g.guide_id, e) for g in GUIDES for e in g.examples]


@pytest.mark.parametrize(
    ("guide_id", "example"), EXAMPLES, ids=[f"{g}-{i}" for i, (g, _) in enumerate(EXAMPLES)]
)
def test_example_is_a_valid_query_call(input_schemas, guide_id, example):
    jsonschema.validate(example.arguments, input_schemas["seattle_data_query_dataset"])
    arguments.check_query({"mode": None, "soql": None, "cursor": None, **example.arguments})


def test_examples_use_their_guide_dataset():
    for guide in GUIDES:
        assert all(e.arguments["dataset_id"] == guide.dataset_id for e in guide.examples)


@pytest.mark.parametrize("guide", GUIDES, ids=[g.guide_id for g in GUIDES])
async def test_guide_reachable_by_tool_and_resource(client, guide):
    tool = wire(
        await client.call_tool_mcp(
            "seattle_data_get_reference", {"topic": "guides", "item_id": guide.guide_id}
        )
    )
    resource = await client.read_resource(f"seattle-data://guides/{guide.guide_id}")
    assert tool["structuredContent"]["body"] == resource[0].text == guide.render()
    assert guide.dataset_id in resource[0].text


async def test_guide_index_entries_carry_dataset_id(client):
    tool = wire(await client.call_tool_mcp("seattle_data_get_reference", {"topic": "guides"}))
    resource = json.loads((await client.read_resource("seattle-data://guides"))[0].text)
    expected = {g.guide_id: g.dataset_id for g in GUIDES}
    for index in (tool["structuredContent"], resource):
        assert {e["item_id"]: e["dataset_id"] for e in index["entries"]} == expected


async def test_non_guide_index_entries_omit_dataset_id(client):
    tool = wire(await client.call_tool_mcp("seattle_data_get_reference", {"topic": "soql"}))
    assert all("dataset_id" not in e for e in tool["structuredContent"]["entries"])


@pytest.mark.soda_fixture("describe_fire_summary")
async def test_describe_links_guide_and_examples(client, soda):
    out = wire(
        await client.call_tool_mcp("seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"})
    )["structuredContent"]
    assert out["guide_uri"] == "seattle-data://guides/fire-911"
    assert out["example_queries"][0]["arguments"]["dataset_id"] == "kzjm-xkqj"


@pytest.mark.soda_fixture("describe_filter_view")
async def test_filter_view_links_parent_guide_without_examples(client, soda):
    out = wire(
        await client.call_tool_mcp(
            "seattle_data_describe_dataset", {"dataset_id": "8tqq-u7ib", "sample_rows": 1}
        )
    )["structuredContent"]
    assert out["guide_uri"] == "seattle-data://guides/building-permits"
    assert "example_queries" not in out


@pytest.mark.soda_fixture("search_fire")
async def test_search_links_guide(client, soda):
    out = wire(
        await client.call_tool_mcp(
            "seattle_data_search_datasets", {"query": "fire 911", "page_size": 3}
        )
    )["structuredContent"]
    fire = next(d for d in out["datasets"] if d["dataset_id"] == "kzjm-xkqj")
    assert fire["guide_uri"] == "seattle-data://guides/fire-911"
