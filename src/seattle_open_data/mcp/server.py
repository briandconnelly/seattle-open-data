"""Server assembly and the failure boundary.

Every tool or resource failure leaves as the PLAN §6 envelope.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.exceptions import McpError, NotFoundError
from fastmcp.server.middleware import Middleware
from pydantic import ValidationError

from seattle_open_data import __version__
from seattle_open_data.constants import CACHE_TTL_SECONDS, SERVER_NAME
from seattle_open_data.core.context import new_request_id
from seattle_open_data.core.errors import (
    ErrorCode,
    ErrorDetails,
    Repair,
    SeattleDataError,
    from_validation_errors,
)
from seattle_open_data.core.reference import INSTRUCTIONS
from seattle_open_data.mcp.resources import register_resources
from seattle_open_data.mcp.result import failure
from seattle_open_data.mcp.tools import register_tools

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext, MiddlewareContext

logger = logging.getLogger("seattle_open_data")

#: MCP folds resource-not-found into Invalid params; -32002 is retired [6.jsonrpc-code-allocation].
_INVALID_PARAMS = -32602
#: Server-defined resource failures sit outside the reserved JSON-RPC range.
_RESOURCE_FAILURE = -31000


def _find(exc: BaseException, kind: type[BaseException]) -> Any:
    """FastMCP wraps handler exceptions in ToolError; look through the cause chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, kind):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _to_error(exc: BaseException, tool: str) -> SeattleDataError:
    if (domain := _find(exc, SeattleDataError)) is not None:
        return domain
    if (invalid := _find(exc, ValidationError)) is not None:
        return from_validation_errors(
            invalid.errors(include_url=False, include_input=False), tool=tool
        )
    logger.exception("unexpected failure in %s", tool)
    return SeattleDataError(ErrorCode.INTERNAL_ERROR, "The server failed unexpectedly.")


class EnvelopeMiddleware(Middleware):
    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        request_id = new_request_id()
        tool = context.message.name
        try:
            return await call_next(context)
        except Exception as exc:
            envelope = _to_error(exc, tool).envelope
            envelope.request_id = request_id
            logger.info("request_id=%s tool=%s code=%s", request_id, tool, envelope.code.value)
            return failure(envelope)

    async def on_read_resource(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        request_id = new_request_id()
        uri = str(context.message.uri)
        try:
            return await call_next(context)
        except McpError:
            raise
        except Exception as exc:
            if (domain := _find(exc, SeattleDataError)) is not None:
                error = domain
                code = (
                    _INVALID_PARAMS
                    if error.envelope.code == ErrorCode.REFERENCE_NOT_FOUND
                    else _RESOURCE_FAILURE
                )
            elif _find(exc, NotFoundError) is not None:
                error = SeattleDataError(
                    ErrorCode.REFERENCE_NOT_FOUND,
                    "No resource at this URI.",
                    details=ErrorDetails(field="uri", reason="Unknown URI."),
                    repair=Repair(
                        next_step="list_topics", tool="seattle_data_get_reference", arguments={}
                    ),
                )
                code = _INVALID_PARAMS
            else:
                logger.exception("unexpected failure reading %s", uri)
                error = SeattleDataError(
                    ErrorCode.INTERNAL_ERROR, "The server failed unexpectedly."
                )
                code = _RESOURCE_FAILURE
            envelope = error.envelope
            envelope.request_id = request_id
            envelope.resource_uri = uri
            raise McpError(
                code=code, message=envelope.message, data=envelope.to_jsonrpc_data()
            ) from exc


def build_server() -> FastMCP:
    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=__version__,
        middleware=[EnvelopeMiddleware()],
        cache_ttl=CACHE_TTL_SECONDS,
        cache_scope="public",
    )
    register_tools(mcp)
    register_resources(mcp)
    return mcp


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
    )
    # FastMCP logs a traceback for every expected domain error; EnvelopeMiddleware logs the rest.
    logging.getLogger("fastmcp.server.server").setLevel(logging.CRITICAL)
    build_server().run(transport="stdio", show_banner=False)
