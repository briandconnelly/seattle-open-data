"""Milestone 1 exit tests (a) schema snapshot and (c) tools/list byte budget."""

from __future__ import annotations

import json
import os
from pathlib import Path

from seattle_open_data.constants import TOOL_PREFIX, TOOLS_LIST_BYTE_BUDGET
from tests.conftest import compact_json, wire

SNAPSHOT = Path(__file__).parent / "snapshots" / "tools_list.json"
EXPECTED_TOOLS = [
    "seattle_data_search_datasets",
    "seattle_data_describe_dataset",
    "seattle_data_query_dataset",
    "seattle_data_get_reference",
]


def _surface(tools_list):
    """The agent-visible tool surface, minus per-response envelope metadata."""
    return {"tools": tools_list["tools"]}


def test_tools_list_matches_snapshot(tools_list):
    current = json.dumps(_surface(tools_list), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(current)
    assert SNAPSHOT.exists(), "no snapshot; create it with UPDATE_SNAPSHOTS=1"
    assert current == SNAPSHOT.read_text(), (
        "tool surface changed; rerun with UPDATE_SNAPSHOTS=1 if intended"
    )


def test_tools_list_within_byte_budget(tools_list):
    size = len(compact_json(tools_list).encode())
    print(f"tools/list serialized size: {size} bytes (budget {TOOLS_LIST_BYTE_BUDGET})")
    assert size <= TOOLS_LIST_BYTE_BUDGET


def test_tool_names_and_order_are_stable(tools_list):
    assert [t["name"] for t in tools_list["tools"]] == EXPECTED_TOOLS
    assert all(name.startswith(TOOL_PREFIX) for name in EXPECTED_TOOLS)


async def test_listing_is_deterministic(client, tools_list):
    again = wire(await client.list_tools_mcp())
    assert compact_json(_surface(again)) == compact_json(_surface(tools_list))


def test_cache_hints_are_public_one_hour(tools_list):
    assert tools_list["ttlMs"] == 3_600_000
    assert tools_list["cacheScope"] == "public"


def test_every_tool_is_read_only_with_explicit_open_world(tools_list):
    for tool in tools_list["tools"]:
        hints = tool["annotations"]
        assert hints["readOnlyHint"] is True
        assert "openWorldHint" in hints
        assert "destructiveHint" not in hints and "idempotentHint" not in hints


def _walk(schema):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _walk(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _walk(item)


def test_input_schemas_are_closed_and_union_free(tools_list):
    for tool in tools_list["tools"]:
        for node in _walk(tool["inputSchema"]):
            assert "anyOf" not in node and "oneOf" not in node, tool["name"]
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, tool["name"]


def test_output_schemas_open_at_top_closed_below(tools_list):
    for tool in tools_list["tools"]:
        schema = tool["outputSchema"]
        assert schema["additionalProperties"] is True, tool["name"]
        assert "source" in schema["required"], tool["name"]
        source = schema["properties"]["source"]
        assert source.get("additionalProperties") is False, tool["name"]


def test_every_parameter_is_described_or_self_evident(tools_list):
    self_evident = {"group_by", "having"}
    for tool in tools_list["tools"]:
        assert len(tool["description"]) > 40
        for name, prop in tool["inputSchema"]["properties"].items():
            assert prop.get("description") or name in self_evident, f"{tool['name']}.{name}"
