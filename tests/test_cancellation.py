"""Cancelling a tool call must release the upstream request, not leave it running."""

from __future__ import annotations

import anyio
import httpx

from tests.test_review_whole import portal


class BlockingStream(httpx.AsyncByteStream):
    """A response body that never arrives; records whether httpx closed it."""

    def __init__(self) -> None:
        self.started, self.closed = anyio.Event(), anyio.Event()

    async def __aiter__(self):
        self.started.set()
        await anyio.sleep_forever()
        yield b"[]"

    async def aclose(self) -> None:
        self.closed.set()


async def test_client_cancel_closes_upstream_stream(client):
    stream = BlockingStream()
    portal(lambda request: httpx.Response(200, stream=stream))
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            client.call_tool_mcp,
            "seattle_data_query_dataset",
            {"dataset_id": "aaaa-bbbb", "select": ["name"]},
        )
        with anyio.fail_after(5):
            await stream.started.wait()
        tg.cancel_scope.cancel()
    with anyio.fail_after(2):
        await stream.closed.wait()
