"""The single error envelope (PLAN §6) and the upstream → envelope mapping.

Tool failures carry `ErrorEnvelope` in `structuredContent` with `isError: true`.
Resource failures carry the same envelope in JSON-RPC `error.data`, with only
`code`/`message` renamed to `machine_code`/`human_message` (see `to_jsonrpc_data`).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class ErrorCode(StrEnum):
    NO_SUCH_COLUMN = "no_such_column"
    COLUMN_NOT_GEOGRAPHIC = "column_not_geographic"
    SOQL_SYNTAX_ERROR = "soql_syntax_error"
    DATASET_NOT_FOUND = "dataset_not_found"
    DATASET_NOT_QUERYABLE = "dataset_not_queryable"
    APP_TOKEN_REJECTED = "app_token_rejected"
    UPSTREAM_FORBIDDEN = "upstream_forbidden"
    RATE_LIMITED = "rate_limited"
    QUERY_TIMEOUT = "query_timeout"
    CURSOR_EXPIRED = "cursor_expired"
    INVALID_CURSOR = "invalid_cursor"
    ROW_TOO_LARGE = "row_too_large"
    INVALID_ARGUMENT = "invalid_argument"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    REFERENCE_NOT_FOUND = "reference_not_found"
    NOT_IMPLEMENTED = "not_implemented"
    INTERNAL_ERROR = "internal_error"


#: Codes whose same call, unchanged, may succeed later [6.retryability].
TEMPORARY_CODES = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.QUERY_TIMEOUT,
        ErrorCode.UPSTREAM_UNAVAILABLE,
    }
)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Repair(_Closed):
    next_step: str
    tool: str
    arguments: dict[str, Any]
    alternative: str | None = None


class ErrorDetails(_Closed):
    field: str | None = None
    fields: list[str] | None = None
    value: Any | None = None
    reason: str
    candidates: list[str] | None = None
    upstream_code: str | None = None
    position: dict[str, int] | None = None


class ErrorEnvelope(_Closed):
    code: ErrorCode
    message: str
    details: ErrorDetails | None = None
    temporary: bool
    retry_after_ms: int | None = Field(default=None, ge=0)
    rate_limit_remaining: int | None = Field(default=None, ge=0)
    repair: Repair | None = None
    request_id: str | None = None
    resource_uri: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """`structuredContent` form: optional fields absent, `retry_after_ms` always present."""
        data = self.model_dump(mode="json", exclude_none=True)
        data["retry_after_ms"] = self.retry_after_ms
        return data

    def to_jsonrpc_data(self) -> dict[str, Any]:
        data = self.to_wire()
        data["machine_code"] = data.pop("code")
        data["human_message"] = data.pop("message")
        return data


class SeattleDataError(Exception):
    """A semantic failure that crosses the MCP boundary as an `ErrorEnvelope`."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: ErrorDetails | None = None,
        retry_after_ms: int | None = None,
        repair: Repair | None = None,
        temporary: bool | None = None,
    ) -> None:
        super().__init__(message)
        is_temporary = code in TEMPORARY_CODES if temporary is None else temporary
        self.envelope = ErrorEnvelope(
            code=code,
            message=message,
            details=details,
            temporary=is_temporary,
            # [6.retryability]: never a delay on a non-temporary error.
            retry_after_ms=retry_after_ms if is_temporary else None,
            repair=repair,
        )


# --------------------------------------------------------------------------
# Upstream (SODA2) failures
# --------------------------------------------------------------------------

_POSITION_KEYS = ("line", "column", "row", "col")


def _soql_position(body: dict[str, Any]) -> dict[str, int] | None:
    data = body.get("data")
    position = body.get("position") or (data.get("position") if isinstance(data, dict) else None)
    if not isinstance(position, dict):
        source = body.get("source")
        position = source.get("position") if isinstance(source, dict) else None
    if not isinstance(position, dict):
        return None
    out = {k: int(v) for k, v in position.items() if k in _POSITION_KEYS and str(v).isdigit()}
    return out or None


_SOQL_CODE = re.compile(r"query\.soql\.[a-z.-]+")
#: Upstream codes are echoed only when they look symbolic [3.sec-sanitize].
_SYMBOLIC = re.compile(r"[a-z0-9_.-]{1,64}")


def _symbolic(value: Any) -> str | None:
    return value if isinstance(value, str) and _SYMBOLIC.fullmatch(value) else None


def _soql_code(body: dict[str, Any]) -> str | None:
    """SODA2 puts the code in `errorCode`, `code`, or only inside `message` text."""
    for key in ("errorCode", "code"):
        value = _symbolic(body.get(key))
        if value is not None and value.startswith("query.soql."):
            return value
    match = _SOQL_CODE.search(str(body.get("message", "")))
    return match.group(0) if match else None


def _retry_after_ms(headers: dict[str, str]) -> int | None:
    raw = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if raw is None or not raw.strip().isdigit():
        return None
    return int(raw.strip()) * 1000


def map_upstream_error(  # noqa: PLR0911 - one return per upstream failure class
    status: int,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
    *,
    dataset_id: str | None = None,
    token_configured: bool = False,
) -> SeattleDataError:
    """Translate a non-2xx SODA2 response into the envelope taxonomy (PLAN §6).

    Upstream message text is never echoed verbatim [3.sec-sanitize]; only the
    symbolic upstream code and its position travel in `details`.
    """
    body = body or {}
    headers = headers or {}

    soql_code = _soql_code(body)
    if soql_code is not None:
        if soql_code.endswith("no-such-column"):
            return SeattleDataError(
                ErrorCode.NO_SUCH_COLUMN,
                "A referenced column does not exist in this dataset.",
                details=ErrorDetails(
                    reason="Use API field names from the dataset schema.",
                    upstream_code=soql_code,
                    position=_soql_position(body),
                ),
                repair=_describe_repair(dataset_id, "inspect_columns", detail="full"),
            )
        return SeattleDataError(
            ErrorCode.SOQL_SYNTAX_ERROR,
            "The upstream service rejected the query.",
            details=ErrorDetails(
                reason="Check the query against the SoQL reference.",
                upstream_code=soql_code,
                position=_soql_position(body),
            ),
            repair=Repair(
                next_step="read_syntax",
                tool="seattle_data_get_reference",
                arguments={"topic": "soql", "item_id": "syntax"},
            ),
        )

    upstream_code = _symbolic(body.get("code"))
    if status == 403:
        message = str(body.get("message", "")).lower()
        if token_configured and "app_token" in message.replace(" ", "_"):
            return SeattleDataError(
                ErrorCode.APP_TOKEN_REJECTED,
                "The configured Socrata application token was rejected.",
                details=ErrorDetails(
                    reason="The operator must replace or remove SOCRATA_APP_TOKEN.",
                    upstream_code=upstream_code,
                ),
                repair=Repair(
                    next_step="read_auth",
                    tool="seattle_data_get_reference",
                    arguments={"topic": "errors", "item_id": "authentication"},
                ),
            )
        return SeattleDataError(
            ErrorCode.UPSTREAM_FORBIDDEN,
            "The upstream service refused access to this resource.",
            details=ErrorDetails(reason="Access denied upstream.", upstream_code=upstream_code),
        )
    if status == 404:
        return SeattleDataError(
            ErrorCode.DATASET_NOT_FOUND,
            "No public Seattle dataset has this id.",
            details=ErrorDetails(
                field="dataset_id",
                value=dataset_id,
                reason="The id may be stale or belong to another portal.",
            )
            if dataset_id
            else None,
            repair=Repair(
                next_step="browse",
                tool="seattle_data_search_datasets",
                arguments={},
                alternative="The id may belong to another Socrata portal; "
                "this server covers data.seattle.gov only.",
            ),
        )
    if status == 429:
        return SeattleDataError(
            ErrorCode.RATE_LIMITED,
            "The upstream service throttled this request.",
            retry_after_ms=_retry_after_ms(headers),
        )
    if status >= 500:
        return SeattleDataError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "The upstream service failed to answer.",
            details=ErrorDetails(reason=f"Upstream HTTP {status}.", upstream_code=upstream_code),
            retry_after_ms=_retry_after_ms(headers),
        )
    return SeattleDataError(
        ErrorCode.INVALID_ARGUMENT,
        "The upstream service rejected the request.",
        details=ErrorDetails(reason=f"Upstream HTTP {status}.", upstream_code=upstream_code),
    )


def _describe_repair(dataset_id: str | None, next_step: str, **arguments: Any) -> Repair | None:
    if dataset_id is None:
        return None
    return Repair(
        next_step=next_step,
        tool="seattle_data_describe_dataset",
        arguments={"dataset_id": dataset_id, **arguments},
    )


# --------------------------------------------------------------------------
# Argument validation failures (raised by the framework before a handler runs)
# --------------------------------------------------------------------------


def from_validation_errors(errors: Sequence[Mapping[str, Any]], *, tool: str) -> SeattleDataError:
    """Build `invalid_argument` from pydantic's `ValidationError.errors()`.

    Received values are never echoed in `details.value`: any parameter can carry a
    mispasted secret, and the error catalog discloses this policy [6.offending-value].
    `details.field` is a published property path (`near.radius_m`) [6.details-field].
    """
    fields: list[str] = []
    reasons: list[str] = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ()) if not isinstance(p, int)]
        path = ".".join(loc) or "arguments"
        if path not in fields:
            fields.append(path)
        reasons.append(f"{path}: {err.get('msg', 'invalid')}")
    details = ErrorDetails(reason="; ".join(reasons)[:500])
    if len(fields) == 1:
        details.field = fields[0]
    else:
        details.fields = fields
    return SeattleDataError(
        ErrorCode.INVALID_ARGUMENT,
        f"Invalid arguments for {tool}.",
        details=details,
    )
