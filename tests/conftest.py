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


class _NoNetwork:
    async def handle_async_request(self, request):
        raise AssertionError(f"unit test tried to reach the network: {request.url}")


@pytest.fixture(autouse=True)
def no_network():
    from seattle_open_data.core import cache
    from seattle_open_data.core.http import SodaClient, set_client

    cache.clear()
    set_client(SodaClient(transport=_NoNetwork()))  # ty: ignore[invalid-argument-type]
    yield
    set_client(None)


@pytest.fixture
def soda(request, no_network):
    """Record or replay the HTTP fixture named by the test's `soda_fixture` marker."""
    from seattle_open_data.core.http import SodaClient, set_client
    from tests.http_fixtures import RECORDING, Recorder, Replay

    marker = request.node.get_closest_marker("soda_fixture")
    assert marker is not None, "use @pytest.mark.soda_fixture(name)"
    name, overrides = marker.args[0], marker.kwargs.get("overrides")
    transport = Recorder(name) if RECORDING and not overrides else Replay(name, overrides)
    set_client(SodaClient(transport=transport))
    yield transport
    set_client(None)
    if isinstance(transport, Recorder):
        transport.save()
