"""Bounded reads and aggregates (docs/design.md: tool 3, Query engine)."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import anyio

from seattle_open_data.constants import (
    DOWNLOAD_LIMIT,
    GEO_TYPES,
    PUBLIC_HOST,
    QUERY_DEADLINE_SECONDS,
    QUERY_LOGICAL_CAP,
    RESULT_BYTE_BUDGET,
    URL_MAX_CHARS,
)
from seattle_open_data.core import cache
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
    from collections.abc import Callable

    from seattle_open_data.core.http import RowStream, SodaClient

_AGGREGATE = re.compile(r"\b(count|sum|avg|min|max|stddev_pop|stddev_samp)\s*\(", re.IGNORECASE)
_IDENTIFIER = re.compile(r"`([^`]*)`|([:@A-Za-z_][\w:@]*)")
_BACKTICKED = re.compile(r"`[^`]*`")
#: A backtick identifier or string literal, in one pass so neither opens the other.
_QUOTED = re.compile(r"`[^`]*`|'(?:[^']|'')*'")
_ALIAS_DECLARATION = re.compile(r"\bas\s+(`[^`]*`|[A-Za-z_]\w*)", re.IGNORECASE)
#: Raw SoQL clause keywords; string literals are blanked before splitting on them.
_CLAUSE = re.compile(
    r"\b(select|where|group\s+by|having|order\s+by|limit|offset|search)\b", re.IGNORECASE
)
#: Clauses where a name declared by `AS` in select means the alias, not a column.
_ALIAS_SCOPE = frozenset({"group by", "having", "order by"})
_DIRECTION = re.compile(r"\s+(asc|desc)(\s+nulls\s+(first|last))?\s*$", re.IGNORECASE)
#: Room for the JSON-RPC envelope and protocol fields around the tool result.
_ENVELOPE_RESERVE = 512
_DEFAULT_PAGE_SIZE = 100

#: The original clauses cannot be echoed back, and a `{dataset_id}`-only query would
#: answer a different question, so the repair reads how to restart instead (#16).
_RESTART = Repair(
    next_step="read_paging",
    tool="seattle_data_get_reference",
    arguments={"topic": "soql", "item_id": "paging"},
    alternative="Resend the first-page call with all its original arguments, without cursor.",
)


async def run(client: SodaClient, *, dataset_id: str, **kwargs: Any) -> QueryResult:
    """One query, bounded as a whole: metadata fetch plus the upstream stream."""
    loaded: list[bool] = []
    try:
        with anyio.fail_after(QUERY_DEADLINE_SECONDS):
            return await _run(client, dataset_id=dataset_id, loaded=loaded, **kwargs)
    except TimeoutError as exc:
        raise SeattleDataError(
            ErrorCode.QUERY_TIMEOUT,
            f"The query did not finish within {QUERY_DEADLINE_SECONDS:.0f} s.",
            # The whole-call deadline can expire first, inside the metadata fetch.
            details=None if loaded else _METADATA_TIMEOUT,
            repair=_narrow(dataset_id) if loaded else None,
        ) from exc


#: Describe waits on the same metadata, so no repair: `temporary` says retry later.
_METADATA_TIMEOUT = ErrorDetails(reason="The dataset's metadata did not load.")


def _narrow(dataset_id: str) -> Repair:
    """Describe finds the time and place columns to narrow on; it returns no rows (#16)."""
    return Repair(
        next_step="inspect_for_narrowing",
        tool="seattle_data_describe_dataset",
        arguments={"dataset_id": dataset_id},
        alternative="Resend the query with every original filter, aggregated server-side "
        "(count(*) with group_by) or split by where on a time column.",
    )


async def _run(
    client: SodaClient,
    *,
    dataset_id: str,
    loaded: list[bool],
    mode: str,
    clauses: dict[str, Any],
    soql: str | None,
    page_size: int | None,
    cursor: str | None,
    detail: Detail,
) -> QueryResult:
    state: dict[str, Any] = {}
    if cursor is not None:
        state = cursors.decode(cursor, "query", restart=_RESTART)
        if state.get("ds") != dataset_id:
            raise SeattleDataError(
                ErrorCode.INVALID_CURSOR,
                "The cursor was issued for a different dataset.",
                details=ErrorDetails(
                    field="cursor", reason="dataset_id does not match the cursor."
                ),
                repair=_RESTART,
            )
        clauses = state["c"]
        mode = "clauses"
    size = page_size or int(state.get("ps", _DEFAULT_PAGE_SIZE))

    try:
        view = await cache.cached_view(client, dataset_id)
    except SeattleDataError as exc:
        if exc.envelope.code == ErrorCode.QUERY_TIMEOUT:
            exc.envelope.details = _METADATA_TIMEOUT
        raise
    loaded.append(True)
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
            cache.views.pop(dataset_id)
        if mode != "soql" and envelope.details is not None:
            # Upstream positions index the assembled query, which the agent never wrote.
            envelope.details.position = None
        if envelope.code == ErrorCode.QUERY_TIMEOUT and envelope.repair is None:
            envelope.repair = _narrow(dataset_id)
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
        coverage_warnings=_warnings(*_written(clauses, where), places) or None,
        source=make_source(retrieved=True),
    )
    if detail == "full":
        _attach_urls(result, dataset_id, params, plan)

    compact = {k: v for k, v in clauses.items() if v is not None}

    def settle(count: int) -> None:
        _reset_page(result, rows[:count])
        emitted_total = emitted_before + count
        if len(stream.rows) <= count:
            return
        if emitted_total >= QUERY_LOGICAL_CAP:
            result.truncated = True
            result.truncation_reason = "row_cap"
            result.truncation_hint = (
                f"Paged queries stop at {QUERY_LOGICAL_CAP:,} rows. Aggregate with "
                "select/group_by, narrow where, or hand the user download_url (detail='full')."
            )
            return
        next_state: dict[str, Any] = {
            "ds": dataset_id,
            "c": compact,
            "n": emitted_total,
            "ps": page_size,
        }
        if plan["kind"] == "keyset":
            next_state["last"] = stream.rows[count - 1].get(":id")
        result.has_more = True
        result.next_cursor = cursors.encode("query", next_state)

    _fit(result, rows, settle)
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
    ordered = {_bare(o) for o in order_by}
    if distinct:
        kind = "offset"
        order = order_by + [k for k in _select_keys(select) if _bare(k) not in ordered]
    elif aggregate:
        kind = "offset"
        order = order_by + [g for g in group_by if _bare(g) not in ordered]
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


_ALIAS = re.compile(r"\s+as\s+(`[^`]*`|[A-Za-z_]\w*)\s*$", re.IGNORECASE)


def _select_keys(select: list[str]) -> list[str]:
    """Each selected column's sort key: its alias, else the expression, without DISTINCT."""
    keys = []
    for index, item in enumerate(select):
        expression = item.strip()
        if index == 0 and expression.lower().startswith("distinct "):
            expression = expression[len("distinct ") :].strip()
        alias = _ALIAS.search(expression)
        keys.append(alias.group(1) if alias else expression)
    return keys


def _bare(expression: str) -> str:
    """A sort key for comparison: no direction, case, or backtick quoting."""
    return _DIRECTION.sub("", expression).strip().strip("`").lower()


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
        coverage_warnings=_warnings(*_soql_reads(soql), places) or None,
        source=make_source(retrieved=True),
    )
    if detail == "full":
        url = f"https://{PUBLIC_HOST}/resource/{dataset_id}.json?" + urlencode(params)
        _set_url(result, "query_url", url)
        result.effective_query = soql

    def settle(count: int) -> None:
        _reset_page(result, rows[:count])
        if stream.more or count < len(rows):
            result.truncated = True
            result.truncation_reason = "raw_soql_not_paged"
            result.truncation_hint = (
                f"Raw SoQL returns one page. Set LIMIT {count} and advance OFFSET by "
                f"{count}, or use clauses mode for cursor paging."
            )

    _fit(result, rows, settle)
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
    return {name: _plain(strip_control(raw.get(name))) for name in names}


def _plain(value: Any) -> Any:
    """ijson yields Decimal for JSON numbers (e.g. GeoJSON coordinates); emit plain numbers."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _reset_page(result: QueryResult, rows: list[dict[str, Any]]) -> None:
    result.rows, result.returned_rows = rows, len(rows)
    result.has_more, result.next_cursor = False, None
    result.truncated, result.truncation_reason, result.truncation_hint = False, None, None


def wire_size(result: QueryResult) -> int:
    """Bytes of the tool result as `mcp/result.success` serializes it, text copy included."""
    payload = result.model_dump(mode="json", exclude_none=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    wire = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": False,
    }
    return len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode())


def _estimate(rows: list[dict[str, Any]], budget: int) -> int:
    """A fast upper bound on how many rows fit; `_fit` corrects it against the real size."""
    used = 0
    for count, row in enumerate(rows):
        used += 2 * len(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode())
        if used > budget:
            return count
    return len(rows)


def _fit(result: QueryResult, rows: list[dict[str, Any]], settle: Callable[[int], None]) -> None:
    """Settle the page on the most whole rows whose complete serialized result fits the budget."""
    budget = RESULT_BYTE_BUDGET - _ENVELOPE_RESERVE
    count = _estimate(rows, budget)
    settle(count)
    while count > 0 and wire_size(result) > budget:
        count -= 1
        settle(count)
    # Also catches a page with no rows whose column descriptors alone overflow the budget.
    if (rows and count == 0) or wire_size(result) > budget:
        raise SeattleDataError(
            ErrorCode.ROW_TOO_LARGE,
            "A single row, or the column list alone, exceeds the response byte budget.",
            details=ErrorDetails(field="select", reason="Select fewer or narrower columns."),
            repair=Repair(
                next_step="project_fewer_columns",
                tool="seattle_data_describe_dataset",
                arguments={"dataset_id": result.dataset_id},
            ),
        )


def _unquote(text: str) -> str:
    """Blank string literals; backtick identifiers stay."""
    return _QUOTED.sub(lambda m: m.group() if m.group().startswith("`") else " ", text)


def _written(clauses: dict[str, Any], where: list[str]) -> tuple[str, str]:
    """Every clause the agent wrote, split as `_soql_reads` splits raw SoQL.

    An omitted select is not scanned.
    """
    later = [clauses.get("having") or ""]
    for key in ("group_by", "order_by"):
        later += clauses.get(key) or []
    return " ".join([*where, *(clauses.get("select") or [])]), " ".join(later)


def _soql_reads(soql: str) -> tuple[str, str]:
    """Raw SoQL as (select and where, the clauses where select's aliases are in scope).

    Keywords inside a backtick identifier, such as an alias named `order by`, are not clauses.
    """
    text = _unquote(soql)
    masked = _BACKTICKED.sub(lambda m: "`" + "_" * (len(m.group()) - 2) + "`", text)
    direct, aliased, start, scope = [], [], 0, ""
    for match in _CLAUSE.finditer(masked):
        (aliased if scope in _ALIAS_SCOPE else direct).append(text[start : match.start()])
        scope, start = " ".join(match.group().lower().split()), match.end()
    (aliased if scope in _ALIAS_SCOPE else direct).append(text[start:])
    return " ".join(direct), " ".join(aliased)


def _names(text: str) -> set[str]:
    """Identifiers read; a backtick identifier is one name, spaces and all."""
    return {quoted or bare for quoted, bare in _IDENTIFIER.findall(text)}


def _warnings(direct: str, aliased: str, places: list[dict[str, Any]]) -> list[CoverageWarning]:
    """Warn on place fields the text reads (docs/design.md: coverage warnings).

    String literals and `AS alias` names are not reads, nor, in `aliased`, a name select
    declared as an alias: `precinct AS neighborhood` then `order_by neighborhood` reads precinct.
    """
    direct, aliased = _unquote(direct), _unquote(aliased)
    aliases = {a.strip("`") for a in _ALIAS_DECLARATION.findall(direct)}
    used = _names(_ALIAS_DECLARATION.sub(" ", direct)) | (_names(aliased) - aliases)
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
