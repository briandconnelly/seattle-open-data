"""Run the server over stdio and record every request a host sends (milestone 1 exit test d).

Usage: python scripts/capture_server.py <capture.jsonl>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware

from seattle_open_data.mcp.server import build_server


class Capture(Middleware):
    def __init__(self, path: Path) -> None:
        self.path = path

    def _write(self, record: dict[str, Any]) -> None:
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    async def on_request(self, context: Any, call_next: Any) -> Any:
        record: dict[str, Any] = {"t": time.time(), "method": context.method}
        try:
            record["params"] = context.message.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        except Exception as exc:  # Capture must never break the server.
            record["capture_error"] = repr(exc)
        try:
            result = await call_next(context)
        except BaseException as exc:
            record["raised"] = type(exc).__name__
            self._write(record)
            raise
        if context.method == "tools/list":
            record["tools"] = [_dump(t) for t in result]
        self._write(record)
        return result


def _dump(tool: Any) -> Any:
    mcp_tool = tool.to_mcp_tool() if hasattr(tool, "to_mcp_tool") else tool
    return mcp_tool.model_dump(mode="json", by_alias=True, exclude_none=True)


def main() -> None:
    server = build_server()
    server.add_middleware(Capture(Path(sys.argv[1])))
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
