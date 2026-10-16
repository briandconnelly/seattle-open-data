from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

from seattle_open_data.mcp.server import build_server


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration", action="store_true", help="run live data.seattle.gov tests"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="needs --run-integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
async def client():
    async with Client(build_server()) as c:
        yield c


def wire(model: Any) -> dict[str, Any]:
    """The JSON a client receives, not the SDK object."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def compact_json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


@pytest.fixture
async def tools_list(client) -> dict[str, Any]:
    return wire(await client.list_tools_mcp())


@pytest.fixture
async def input_schemas(tools_list) -> dict[str, dict[str, Any]]:
    return {t["name"]: t["inputSchema"] for t in tools_list["tools"]}
