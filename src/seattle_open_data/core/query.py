"""Bounded reads and aggregates (PLAN §4 tool 3, §5 paging)."""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from seattle_open_data.constants import (
    DOWNLOAD_LIMIT,
    GEO_TYPES,
    PUBLIC_HOST,
    QUERY_LOGICAL_CAP,
    RESULT_BYTE_BUDGET,
    URL_MAX_CHARS,
)
from seattle_open_data.core import cursor as cursors
from seattle_open_data.core.context import make_source
from seattle_open_data.core.describe import place_specs, require_queryable
from seattle_open_data.core.errors import (
    ErrorCode,
    ErrorDetails,
    Repair,
    SeattleDataError,
)
from seattle_open_data.core.models import CoverageWarning, Detail, QueryResult, ResultColumn
from seattle_open_data.core.text import strip_control

if TYPE_CHECKING:
    from seattle_open_data.core.http import RowStream, SodaClient

_AGGREGATE = re.compile(r"\b(count|sum|avg|min|max|stddev_pop|stddev_samp)\s*\(", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[:@A-Za-z_][\w:@]*")
_DIRECTION = re.compile(r"\s+(asc|desc)(\s+nulls\s+(first|last))?\s*$", re.IGNORECASE)
_VIEW_TTL_SECONDS = 3600
#: Room kept for the cursor and flags when packing rows against the byte budget.
_ENVELOPE_RESERVE = 1024
_TOOL = "seattle_data_query_dataset"
_DEFAULT_PAGE_SIZE = 100

_views: dict[str, tuple[float, dict[str, Any]]] = {}


async def cached_view(client: SodaClient, dataset_id: str) -> dict[str, Any]:
    hit = _views.get(dataset_id)
    if hit and time.monotonic() - hit[0] < _VIEW_TTL_SECONDS:
        return hit[1]
    view = await client.view(dataset_id)
    _views[dataset_id] = (time.monotonic(), view)
    return view


def forget_view(dataset_id: str) -> None:
    _views.pop(dataset_id, None)


def _restart(dataset_id: str) -> Repair:
    return Repair(
        next_step="restart_from_first_page",
        tool=_TOOL,
        arguments={"dataset_id": dataset_id},
        alternative="Resend the original query arguments without cursor.",
    )


async def run(
    client: SodaClient,
    *,
    dataset_id: str,
    mode: str,
    clauses: dict[str, Any],
    soql: str | None,
    page_size: int | None,
    cursor: str | None,
    detail: Detail,
) -> QueryResult:
    state: dict[str, Any] = {}
    if cursor is not None:
        state = cursors.decode(cursor, "query", restart=_restart(dataset_id))
        if state.get("ds") != dataset_id:
            raise SeattleDataError(
                ErrorCode.INVALID_CURSOR,
                "The cursor was issued for a different dataset.",
                details=ErrorDetails(
                    field="cursor", reason="dataset_id does not match the cursor."
                ),
                repair=_restart(dataset_id),
            )
        clauses = state["c"]
        mode = "clauses"
    size = page_size or int(state.get("ps", _DEFAULT_PAGE_SIZE))

    view = await cached_view(client, dataset_id)
    require_queryable(view, dataset_id)
    columns = view.get("columns") or []
    places = place_specs(view.get("modifyingViewUid") or dataset_id, columns)

    try:
        if mode == "soql" and soql is not None:
            return await _run_soql(client, dataset_id, soql, size, detail, places)
        return await _run_clauses(client, dataset_id, clauses, state, size, detail, columns, places)
    except SeattleDataError as exc:
        envelope = exc.envelope
        if envelope.code == ErrorCode.NO_SUCH_COLUMN:
            forget_view(dataset_id)
        if mode != "soql" and envelope.details is not None:
            # Upstream positions index the assembled query, which the agent never wrote.
            envelope.details.position = None
        if envelope.code == ErrorCode.QUERY_TIMEOUT and envelope.repair is None:
            envelope.repair = Repair(
                next_step="narrow",
                tool=_TOOL,
                arguments={"dataset_id": dataset_id},
                alternative="Aggregate server-side (count(*) with group_by) "
                "or add a tighter where on a time column.",
            )
        raise


# --------------------------------------------------------------------------
# Clauses mode
# --------------------------------------------------------------------------


async def _run_clauses(
    client: SodaClient,
    dataset_id: str,
    clauses: dict[str, Any],
    state: dict[str, Any],
    page_size: int,
    detail: Detail,
    columns: list[dict[str, Any]],
    places: list[dict[str, Any]],
) -> QueryResult:
    plan = _plan(dataset_id, clauses, columns, places)
    emitted_before = int(state.get("n", 0))
    fetch = min(page_size, QUERY_LOGICAL_CAP - emitted_before)

    params = dict(plan["params"])
    where = list(plan["where"])
    if plan["kind"] == "keyset" and state.get("last"):
        last = str(state["last"]).replace("'", "''")
        where.append(f":id > '{last}'")
    if where:
        params["$where"] = " AND ".join(where)
    params["$limit"] = fetch + 1
    if plan["kind"] == "offset" and emitted_before:
        params["$offset"] = emitted_before

    stream = await client.stream_rows(dataset_id, params, max_rows=fetch + 1)
    names, types = _visible(stream, plan["hidden"])
    rows = [_row(r, names) for r in stream.rows[:fetch]]

    result = QueryResult(
        dataset_id=dataset_id,
        columns=[ResultColumn(name=n, type=t) for n, t in zip(names, types, strict=True)],
        rows=[],
        returned_rows=0,
        has_more=False,
        truncated=False,
        consistency="stable" if plan["kind"] == "keyset" else "best_effort",
        coverage_warnings=_warnings(" ".join([*where, clauses.get("having") or ""]), places)
        or None,
        source=make_source(retrieved=True),
    )
    if detail == "full":
        _attach_urls(result, dataset_id, params, plan)

    packed = _pack(result, rows)
    result.rows, result.returned_rows = packed, len(packed)
    emitted_total = emitted_before + len(packed)
    more = len(stream.rows) > len(packed)
    if more and emitted_total >= QUERY_LOGICAL_CAP:
        result.truncated = True
        result.truncation_reason = "row_cap"
        result.truncation_hint = (
            f"Paged queries stop at {QUERY_LOGICAL_CAP:,} rows. Aggregate with select/group_by, "
            "narrow where, or hand the user download_url (detail='full')."
        )
    elif more:
        compact = {k: v for k, v in clauses.items() if v is not None}
        next_state: dict[str, Any] = {
            "ds": dataset_id,
            "c": compact,
            "n": emitted_total,
            "ps": page_size,
        }
        if plan["kind"] == "keyset":
            next_state["last"] = stream.rows[len(packed) - 1].get(":id")
        result.has_more = True
        result.next_cursor = cursors.encode("query", next_state)
    return result


def _plan(
    dataset_id: str,
    clauses: dict[str, Any],
    columns: list[dict[str, Any]],
    places: list[dict[str, Any]],
) -> dict[str, Any]:
    types: dict[str, Any] = {str(c.get("fieldName")): c.get("dataTypeName") for c in columns}
    select = list(clauses.get("select") or [n for n in types if n and not n.startswith(":")])
    group_by = list(clauses.get("group_by") or [])
    order_by = list(clauses.get("order_by") or [])
    distinct = bool(select) and select[0].strip().lower().startswith("distinct ")
    aggregate = bool(group_by) or distinct or any(_AGGREGATE.search(s) for s in select)
    if clauses.get("having"):
        aggregate = True

    where: list[str] = []
    if clauses.get("where"):
        where.append(f"({clauses['where']})")
    near = clauses.get("near")
    if near:
        where.append(_within_circle(dataset_id, near, types, places))

    hidden: set[str] = set()
    if aggregate:
        kind = "offset"
        order = order_by + [g for g in group_by if _bare(g) not in {_bare(o) for o in order_by}]
    elif order_by:
        kind = "offset"
        order = [*order_by, ":id"]
    else:
        kind = "keyset"
        order = [":id"]
        if ":id" not in select:
            select.append(":id")
            hidden.add(":id")

    params: dict[str, Any] = {"$select": ", ".join(select)}
    if group_by:
        params["$group"] = ", ".join(group_by)
    if clauses.get("having"):
        params["$having"] = clauses["having"]
    if order:
        params["$order"] = ", ".join(order)
    if clauses.get("search_text"):
        params["$q"] = clauses["search_text"]
    return {"params": params, "where": where, "kind": kind, "hidden": hidden}


def _bare(expression: str) -> str:
    return _DIRECTION.sub("", expression).strip().lower()


def _within_circle(
    dataset_id: str,
    near: dict[str, Any],
    types: dict[str, Any],
    places: list[dict[str, Any]],
) -> str:
    column = near["column"]
    kind = types.get(column)
    if kind not in GEO_TYPES:
        place_names = [p["column"] for p in places if p["source"] == "column"]
        alternative = (
            "Filter on a place field instead (" + ", ".join(place_names) + "); "
            "a neighborhood is not equivalent to a radius."
            if place_names
            else "This dataset has no point or location column; near is unavailable."
        )
        raise SeattleDataError(
            ErrorCode.COLUMN_NOT_GEOGRAPHIC,
            "near needs a point or location column.",
            details=ErrorDetails(
                field="near.column",
                value=column if kind is not None else None,
                reason=f"Column type is {kind}." if kind else "No such column in this dataset.",
            ),
            repair=Repair(
                next_step="inspect_geography",
                tool="seattle_data_describe_dataset",
                arguments={"dataset_id": dataset_id, "detail": "full"},
                alternative=alternative,
            ),
        )
    return (
        f"within_circle({column}, {float(near['latitude'])!r}, "
        f"{float(near['longitude'])!r}, {float(near['radius_m'])!r})"
    )


# --------------------------------------------------------------------------
# Raw SoQL mode
# --------------------------------------------------------------------------


async def _run_soql(
    client: SodaClient,
    dataset_id: str,
    soql: str,
    page_size: int,
    detail: Detail,
    places: list[dict[str, Any]],
) -> QueryResult:
    params = {"$query": soql}
    stream = await client.stream_rows(dataset_id, params, max_rows=page_size)
    names, types = _visible(stream, set())
    rows = [_row(r, names) for r in stream.rows]
    result = QueryResult(
        dataset_id=dataset_id,
        columns=[ResultColumn(name=n, type=t) for n, t in zip(names, types, strict=True)],
        rows=[],
        returned_rows=0,
        has_more=False,
        truncated=False,
        consistency="single_page",
        coverage_warnings=_warnings(soql, places) or None,
        source=make_source(retrieved=True),
    )
    if detail == "full":
        url = f"https://{PUBLIC_HOST}/resource/{dataset_id}.json?" + urlencode(params)
        _set_url(result, "query_url", url)
        result.effective_query = soql
    packed = _pack(result, rows)
    result.rows, result.returned_rows = packed, len(packed)
    if stream.more or len(packed) < len(rows):
        result.truncated = True
        result.truncation_reason = "raw_soql_not_paged"
        result.truncation_hint = (
            f"Raw SoQL returns one page. Set LIMIT {len(packed)} and advance OFFSET by "
            f"{len(packed)}, or use clauses mode for cursor paging."
        )
    return result


# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------


def _visible(stream: RowStream, hidden: set[str]) -> tuple[list[str], list[str]]:
    fields = stream.fields or (list(stream.rows[0]) if stream.rows else [])
    types = stream.types if len(stream.types) == len(fields) else ["unknown"] * len(fields)
    pairs = [(f, t) for f, t in zip(fields, types, strict=True) if f not in hidden]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _row(raw: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """SODA2 omits null keys; every row gets every visible column."""
    return {name: strip_control(raw.get(name)) for name in names}


def _pack(result: QueryResult, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fit whole rows so the final tool result, which carries the JSON twice, stays in budget."""
    base = len(json.dumps(result.model_dump(mode="json", exclude_none=True), separators=(",", ":")))
    budget = RESULT_BYTE_BUDGET // 2 - base - _ENVELOPE_RESERVE
    packed: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        size = len(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode()) + 1
        if used + size > budget:
            break
        packed.append(row)
        used += size
    if rows and not packed:
        raise SeattleDataError(
            ErrorCode.ROW_TOO_LARGE,
            "A single row exceeds the response byte budget.",
            details=ErrorDetails(field="select", reason="Select fewer or narrower columns."),
            repair=Repair(
                next_step="project_fewer_columns",
                tool="seattle_data_describe_dataset",
                arguments={"dataset_id": result.dataset_id},
            ),
        )
    return packed


def _warnings(text: str, places: list[dict[str, Any]]) -> list[CoverageWarning]:
    used = set(_IDENTIFIER.findall(text))
    return [
        CoverageWarning(
            column=p["column"],
            reliability=p["reliability"],
            reason=p.get("reason") or "This place field has known gaps.",
        )
        for p in places
        if p["reliability"] != "ok" and p["column"] in used
    ]


def _attach_urls(
    result: QueryResult, dataset_id: str, params: dict[str, Any], plan: dict[str, Any]
) -> None:
    base = f"https://{PUBLIC_HOST}/resource/{dataset_id}"
    _set_url(result, "query_url", f"{base}.json?" + urlencode(params))
    download = {k: v for k, v in plan["params"].items()}
    if plan["where"]:
        download["$where"] = " AND ".join(plan["where"])
    download["$limit"] = DOWNLOAD_LIMIT
    _set_url(result, "download_url", f"{base}.csv?" + urlencode(download))
    parts = [f"SELECT {params['$select']}"]
    for key, word in (
        ("$where", "WHERE"),
        ("$group", "GROUP BY"),
        ("$having", "HAVING"),
        ("$order", "ORDER BY"),
    ):
        if key in params:
            parts.append(f"{word} {params[key]}")
    if "$q" in params:
        parts.append(f"SEARCH {json.dumps(params['$q'])}")
    parts.append(f"LIMIT {params['$limit']}")
    if "$offset" in params:
        parts.append(f"OFFSET {params['$offset']}")
    result.effective_query = " ".join(parts)


def _set_url(result: QueryResult, name: str, url: str) -> None:
    if len(url) <= URL_MAX_CHARS:
        setattr(result, name, url)
    else:
        setattr(result, f"{name}_omitted", True)
