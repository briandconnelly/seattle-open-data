"""Cross-argument rules the JSON Schema cannot express portably (docs/design.md: Primitives).

Single-field constraints (types, enums, ranges, patterns, empty strings) live in
the published inputSchema; only combinations are checked here.
"""

from __future__ import annotations

from typing import Any

from seattle_open_data.constants import QUERY_TEXT_MAX_BYTES
from seattle_open_data.core import cursor as cursors
from seattle_open_data.core.errors import ErrorCode, ErrorDetails, Repair, SeattleDataError

CLAUSE_ARGS = ("select", "where", "group_by", "having", "order_by", "search_text", "near")


def _provided(args: dict[str, Any]) -> list[str]:
    return [name for name, value in args.items() if value is not None]


def _reject(fields: list[str], reason: str, *, tool: str, repair: Repair | None = None) -> None:
    details = (
        ErrorDetails(field=fields[0], reason=reason)
        if len(fields) == 1
        else ErrorDetails(fields=fields, reason=reason)
    )
    raise SeattleDataError(
        ErrorCode.INVALID_ARGUMENT,
        f"Invalid argument combination for {tool}.",
        details=details,
        repair=repair,
    )


def check_continuation(args: dict[str, Any], *, allowed: set[str], tool: str) -> None:
    if args.get("cursor") is None:
        return
    extra = sorted(set(_provided(args)) - allowed - {"cursor"})
    if extra:
        _reject(
            extra,
            "A cursor continues the original query; send only "
            + ", ".join(sorted(allowed | {"cursor"}))
            + ".",
            tool=tool,
        )


def check_search(args: dict[str, Any]) -> None:
    check_continuation(args, allowed={"detail", "page_size"}, tool="seattle_data_search_datasets")


def check_query(args: dict[str, Any]) -> None:
    tool = "seattle_data_query_dataset"
    check_continuation(args, allowed={"dataset_id", "detail", "page_size"}, tool=tool)
    if args.get("cursor") is not None:
        return
    mode = args.get("mode") or "clauses"
    if mode == "soql":
        if args.get("soql") is None:
            _reject(["soql"], "mode='soql' requires soql.", tool=tool)
        clauses = [name for name in CLAUSE_ARGS if args.get(name) is not None]
        if clauses:
            _reject(
                clauses,
                "mode='soql' runs soql verbatim; clause arguments are not allowed.",
                tool=tool,
            )
        return
    if args.get("soql") is not None:
        _reject(["soql"], "soql requires mode='soql'.", tool=tool)
    # Measured as a cursor stores the clauses, so the cap also bounds the cursor (≤ 6 KiB).
    present = {n: _plain(args[n]) for n in CLAUSE_ARGS if args.get(n) is not None}
    if len(cursors.dump(present)) > QUERY_TEXT_MAX_BYTES:
        _reject(
            list(present),
            f"Clause arguments exceed {QUERY_TEXT_MAX_BYTES} bytes in total, measured as JSON.",
            tool=tool,
        )


def check_reference(args: dict[str, Any]) -> None:
    tool = "seattle_data_get_reference"
    topic = args.get("topic") or "index"
    if args.get("item_id") is not None and topic in ("index", "capabilities"):
        _reject(
            ["topic", "item_id"],
            f"Topic '{topic}' has no items; item_id needs soql, errors, or guides.",
            tool=tool,
            repair=Repair(next_step="list_topics", tool=tool, arguments={}),
        )


def _plain(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value
