"""Reference content: tool and resources serve one copy; instructions keep their binding prefix."""

from __future__ import annotations

import json

import pytest

from seattle_open_data.constants import INSTRUCTIONS_SENTINEL
from seattle_open_data.core.reference import CAPABILITIES, INSTRUCTIONS, TOPICS
from tests.conftest import wire

DOCUMENTS = [(topic, doc.item_id) for topic, t in TOPICS.items() for doc in t.documents]


@pytest.mark.parametrize(("topic", "item_id"), DOCUMENTS)
async def test_tool_and_resource_serve_the_same_body(client, topic, item_id):
    tool = wire(
        await client.call_tool_mcp(
            "seattle_data_get_reference", {"topic": topic, "item_id": item_id}
        )
    )
    resource = await client.read_resource(f"seattle-data://{topic}/{item_id}")
    assert tool["isError"] is False
    assert tool["structuredContent"]["body"] == resource[0].text
    assert tool["structuredContent"]["uri"] == f"seattle-data://{topic}/{item_id}"


async def test_index_entries_resolve_as_resources(client):
    index = wire(await client.call_tool_mcp("seattle_data_get_reference", {}))["structuredContent"]
    assert {e["item_id"] for e in index["entries"]} == set(TOPICS)
    for entry in index["entries"]:
        assert await client.read_resource(entry["uri"])
    reference = json.loads((await client.read_resource(index["uri"]))[0].text)
    assert reference["entries"] == index["entries"]


async def test_capabilities_resource(client):
    assert (await client.read_resource("seattle-data://capabilities"))[0].text == CAPABILITIES


def test_instructions_end_with_sentinel():
    assert INSTRUCTIONS.splitlines()[-1] == INSTRUCTIONS_SENTINEL


def test_instructions_lead_with_negative_scope():
    head = INSTRUCTIONS[:400]
    assert "Does NOT" in head
    assert "performance.seattle.gov" in head


async def test_instructions_reach_the_client(client):
    assert client.instructions == INSTRUCTIONS


def test_capabilities_names_only_real_tools():
    import re

    named = set(re.findall(r"seattle_data_[a-z_]+", CAPABILITIES + INSTRUCTIONS))
    assert named <= {
        "seattle_data_search_datasets",
        "seattle_data_describe_dataset",
        "seattle_data_query_dataset",
        "seattle_data_get_reference",
    }


def test_query_description_states_the_real_cap():
    from seattle_open_data.constants import QUERY_LOGICAL_CAP
    from seattle_open_data.mcp.tools import seattle_data_query_dataset

    assert f"{QUERY_LOGICAL_CAP:,} rows" in (seattle_data_query_dataset.__doc__ or "")
