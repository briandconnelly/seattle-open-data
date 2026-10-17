"""Regressions for the Copilot review of PR #1."""

from __future__ import annotations

import httpx
import pytest

from seattle_open_data.constants import UPSTREAM_MAX_BYTES
from seattle_open_data.core import cache, catalog
from seattle_open_data.core.errors import ErrorCode, SeattleDataError
from seattle_open_data.core.http import SodaClient, set_client
from tests.conftest import wire


async def test_whitespace_prefix_counts_against_the_size_cap():
    chunk = b" " * (1024 * 1024)
    served = 0

    async def body():
        nonlocal served
        for _ in range(UPSTREAM_MAX_BYTES // len(chunk) + 4):
            served += 1
            yield chunk
        yield b"[]"

    client = SodaClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body()))
    )
    with pytest.raises(SeattleDataError) as caught:
        await client.stream_rows("aaaa-bbbb", {}, max_rows=10)
    assert caught.value.envelope.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert served <= UPSTREAM_MAX_BYTES // len(chunk) + 1


def item(dataset_id: str, kind: str = "dataset", parent: str | None = None) -> dict:
    resource = {"id": dataset_id, "name": dataset_id, "type": kind}
    if parent:
        resource["parent_fxf"] = [parent]
    return {"resource": resource, "permalink": f"https://data.seattle.gov/d/{dataset_id}"}


def catalog_client(pages: dict[int, list[dict]], total: int, lookups: list):
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params.multi_items())
        if "ids" in params:
            lookups.append(request.url.params.get_list("ids"))
            ids = request.url.params.get_list("ids")
            return httpx.Response(
                200, json={"results": [item(i) for i in ids], "resultSetSize": len(ids)}
            )
        return httpx.Response(
            200, json={"results": pages[int(params["offset"])], "resultSetSize": total}
        )

    return SodaClient(transport=httpx.MockTransport(handler))


async def test_map_on_a_later_page_does_not_repeat_its_parent():
    lookups: list = []
    client = catalog_client(
        {0: [item("aaaa-aaaa")], 1: [item("mmmm-mmmm", "map", "aaaa-aaaa")]}, 2, lookups
    )
    args = {"query": "x", "asset_scope": "tabular", "category": None, "detail": "summary"}
    first = await catalog.search(client, page_size=1, cursor=None, **args)
    assert [d.dataset_id for d in first.datasets] == ["aaaa-aaaa"]
    second = await catalog.search(client, page_size=None, cursor=first.next_cursor, **args)
    assert second.datasets == []
    assert lookups == [], "an already-emitted parent is not looked up again"


async def test_map_ranked_above_its_parent_keeps_that_rank():
    lookups: list = []
    page = [item("mmmm-mmmm", "map", "aaaa-aaaa"), item("bbbb-bbbb"), item("aaaa-aaaa")]
    client = catalog_client({0: page}, 3, lookups)
    out = await catalog.search(
        client,
        query="x",
        asset_scope="tabular",
        category=None,
        page_size=10,
        cursor=None,
        detail="summary",
    )
    assert [d.dataset_id for d in out.datasets] == ["aaaa-aaaa", "bbbb-bbbb"]
    assert lookups == []


@pytest.mark.parametrize("tool", ["seattle_data_describe_dataset", "seattle_data_query_dataset"])
async def test_error_object_metadata_is_upstream_unavailable_and_not_cached(client, tool):
    def handler(request):
        if "/api/views/" in request.url.path:
            return httpx.Response(200, json={"error": True, "message": "upstream failure"})
        return httpx.Response(200, json=[])

    cache.clear()
    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    result = wire(await client.call_tool_mcp(tool, {"dataset_id": "aaaa-bbbb"}))
    assert result["structuredContent"]["code"] == "upstream_unavailable"
    assert "aaaa-bbbb" not in cache.views
