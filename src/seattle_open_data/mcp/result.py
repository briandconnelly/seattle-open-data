"""Tool results: `structuredContent` is the contract; `content[0]` is its serialized copy.

See [3.content-types].
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastmcp.tools import ToolResult
from mcp.types import TextContent

if TYPE_CHECKING:
    from pydantic import BaseModel

    from seattle_open_data.core.errors import ErrorEnvelope


def _result(payload: dict[str, Any], *, is_error: bool) -> ToolResult:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        is_error=is_error,
    )


def success(model: BaseModel) -> ToolResult:
    return _result(model.model_dump(mode="json", exclude_none=True), is_error=False)


def failure(envelope: ErrorEnvelope) -> ToolResult:
    return _result(envelope.to_wire(), is_error=True)
