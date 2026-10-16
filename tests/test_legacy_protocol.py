"""Codex CLI connects with the legacy initialize handshake (docs/host-captures); keep it working."""

from __future__ import annotations

from fastmcp import Client

from seattle_open_data.core.reference import INSTRUCTIONS
from seattle_open_data.mcp.server import build_server
from tests.conftest import wire


async def test_legacy_handshake_serves_the_same_contract():
    async with Client(build_server(), mode="legacy") as legacy, Client(build_server()) as modern:
        assert legacy.initialize_result is not None
        assert not (legacy.protocol_version or "").startswith("2026")
        assert legacy.initialize_result.instructions == INSTRUCTIONS

        legacy_tools = wire(await legacy.list_tools_mcp())["tools"]
        modern_tools = wire(await modern.list_tools_mcp())["tools"]
        assert legacy_tools == modern_tools

        result = wire(
            await legacy.call_tool_mcp(
                "seattle_data_query_dataset", {"dataset_id": "kzjm-xkqj", "mode": "soql"}
            )
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "invalid_argument"
