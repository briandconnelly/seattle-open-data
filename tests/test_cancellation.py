"""Cancelling a tool call must release the upstream request, not leave it running."""

from __future__ import annotations

import anyio
import httpx

from tests.test_review_whole import portal


async def test_client_cancel_closes_upstream_stream(client):
    started, closed = anyio.Event(), anyio.Event()

    async def blocking_body():
        started.set()
        try:
            await anyio.sleep_forever()
            yield b"[]"
        finally:
            closed.set()

    portal(lambda request: httpx.Response(200, content=blocking_body()))
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            client.call_tool_mcp,
            "seattle_data_query_dataset",
            {"dataset_id": "aaaa-bbbb", "select": ["name"]},
        )
        with anyio.fail_after(5):
            await started.wait()
        tg.cancel_scope.cancel()
    with anyio.fail_after(2):
        await closed.wait()
