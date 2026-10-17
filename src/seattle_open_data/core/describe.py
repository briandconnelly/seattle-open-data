"""Dataset inspection (docs/design.md: tool 2).

Live aggregates run concurrently under one deadline; a slow dataset degrades to
cached or absent values with an explicit status, never to a silent zero.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

import anyio

from seattle_open_data.constants import (
    DESCRIBE_AGGREGATE_TIMEOUT_SECONDS,
    DESCRIBE_DEADLINE_SECONDS,
    GEO_TYPES,
    PLACE_FIELDS,
    PLACEHOLDER_VALUES,
    REGION_LAYERS,
    TIME_EVIDENCE,
    TIME_TYPES,
)
from seattle_open_data.core import cache
from seattle_open_data.core.catalog import queryability
from seattle_open_data.core.context import make_source
from seattle_open_data.core.errors import (
    ErrorCode,
    ErrorDetails,
    Repair,
    SeattleDataError,
)
from seattle_open_data.core.guides import BY_DATASET
from seattle_open_data.core.http import shape_error
from seattle_open_data.core.models import (
    Column,
    ColumnStats,
    Coverage,
    DescribeResult,
    Detail,
    ExampleQuery,
    GeoColumn,
    GeoUnusable,
    PlaceField,
    PlaceholderValue,
    PlaceValue,
    RowCount,
    TimeSemantics,
)
from seattle_open_data.core.reference import topic_uri
from seattle_open_data.core.text import clean, html_to_text, strip_control

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from seattle_open_data.core.http import SodaClient

_COORDINATE = re.compile(r"(^|_)(lat|latitude|lon|lng|long|longitude)$")
_LABEL_FETCH_TIMEOUT = 5.0
_MAX_VALUES = 100
_PLACE_CONCURRENCY = 4


class _Outcome:
    """Result of one bounded upstream aggregate."""

    def __init__(self) -> None:
        self.value: Any = None
        self.status: Literal["ok", "timeout", "unavailable"] = "timeout"
        self.error: SeattleDataError | None = None


#: Failures the caller must see as the result of describe itself, not as a degraded field.
_ACTIONABLE = frozenset(
    {ErrorCode.APP_TOKEN_REJECTED, ErrorCode.UPSTREAM_FORBIDDEN, ErrorCode.RATE_LIMITED}
)


async def _bounded(outcome: _Outcome, work: Callable[..., Awaitable[Any]], *args: Any) -> None:
    try:
        outcome.value = await work(*args)
        outcome.status = "ok"
    except SeattleDataError as exc:
        outcome.error = exc
        outcome.status = (
            "timeout" if exc.envelope.code == ErrorCode.QUERY_TIMEOUT else "unavailable"
        )


async def _limited(
    limiter: anyio.CapacityLimiter,
    outcome: _Outcome,
    work: Callable[..., Awaitable[Any]],
    *args: Any,
) -> None:
    # The coroutine is created only after a slot frees, so a deadline never strands one.
    async with limiter:
        await _bounded(outcome, work, *args)


def _raise_actionable(outcomes: Iterable[_Outcome]) -> None:
    for outcome in outcomes:
        if outcome.error is not None and outcome.error.envelope.code in _ACTIONABLE:
            raise outcome.error


async def describe(client: SodaClient, *, dataset_id: str, **kwargs: Any) -> DescribeResult:
    """Bounded as a whole; the per-phase deadlines inside keep partial results useful."""
    try:
        with anyio.fail_after(DESCRIBE_DEADLINE_SECONDS):
            return await _describe(client, dataset_id=dataset_id, **kwargs)
    except TimeoutError as exc:
        raise SeattleDataError(
            ErrorCode.QUERY_TIMEOUT,
            f"Describe did not finish within {DESCRIBE_DEADLINE_SECONDS:.0f} s.",
        ) from exc


async def _describe(
    client: SodaClient,
    *,
    dataset_id: str,
    column_query: str | None,
    sample_rows: int,
    detail: Detail,
) -> DescribeResult:
    # Always fresh: describe is where an agent checks the schema, and it reseeds query's copy.
    view = await cache.fresh_view(client, dataset_id)
    require_queryable(view, dataset_id)

    # Filter views inherit their parent's curated facts.
    facts_id = view.get("modifyingViewUid") or dataset_id
    all_columns = view.get("columns") or []
    data_columns = [c for c in all_columns if not str(c.get("fieldName", "")).startswith(":")]
    time_columns = [c for c in data_columns if c.get("dataTypeName") in TIME_TYPES]
    coverage_columns = _coverage_columns(facts_id, time_columns)
    places = place_specs(facts_id, all_columns)
    guide = BY_DATASET.get(facts_id)

    row_count = _Outcome()
    coverage = {c["fieldName"]: _Outcome() for c in coverage_columns}
    sample = _Outcome()
    # Two phases: on the 11M-row SPD dataset, running place-field grouping alongside
    # the row count made even the row count time out.
    with anyio.move_on_after(DESCRIBE_AGGREGATE_TIMEOUT_SECONDS):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_bounded, row_count, _count, client, dataset_id)
            for name, outcome in coverage.items():
                tg.start_soon(_bounded, outcome, _min_max, client, dataset_id, name)
            if sample_rows:
                tg.start_soon(
                    _bounded, sample, _sample, client, dataset_id, data_columns, sample_rows
                )
    _raise_actionable([row_count, sample, *coverage.values()])

    place_stats = {p["column"]: _Outcome() for p in places} if detail == "full" else {}
    if place_stats:
        limiter = anyio.CapacityLimiter(_PLACE_CONCURRENCY)
        with anyio.move_on_after(DESCRIBE_AGGREGATE_TIMEOUT_SECONDS):
            async with anyio.create_task_group() as tg:
                for name, outcome in place_stats.items():
                    tg.start_soon(
                        _limited, limiter, outcome, _value_groups, client, dataset_id, name
                    )
        _raise_actionable(place_stats.values())

    labels = await _region_labels(client, places, place_stats) if place_stats else {}

    columns = [
        _column(c, detail)
        for c in data_columns
        if column_query is None or _matches(c, column_query)
    ]
    return DescribeResult(
        dataset_id=dataset_id,
        title=clean(view.get("name"), limit=200) or dataset_id,
        queryability="supported",
        row_count=_row_count(row_count, all_columns),
        data_coverage=[_coverage(c, coverage[c["fieldName"]]) for c in coverage_columns],
        columns=columns,
        stats_available=any(c.get("cachedContents") for c in data_columns),
        place_fields=[_place_field(p, place_stats.get(p["column"]), labels) for p in places],
        geo_columns=[
            GeoColumn(column=c["fieldName"], type=c["dataTypeName"])
            for c in data_columns
            if c.get("dataTypeName") in GEO_TYPES
        ],
        geo_unusable=_geo_unusable(data_columns) or None,
        time_semantics=[_time(facts_id, c) for c in time_columns],
        sample_rows=_fill(sample.value, data_columns)
        if sample_rows and sample.status == "ok"
        else None,
        guide_uri=topic_uri("guides", guide.guide_id) if guide else None,
        # Examples name their dataset: a filter view links its parent's guide, without examples.
        example_queries=[
            ExampleQuery(question=e.question, arguments=e.arguments) for e in guide.examples
        ]
        if guide and guide.dataset_id == dataset_id
        else None,
        source=make_source(retrieved=True),
    )


def require_queryable(view: dict[str, Any], dataset_id: str) -> None:
    asset_type = str(view.get("assetType") or view.get("viewType") or "unknown")
    if queryability(asset_type) == "supported" and view.get("viewType") == "tabular":
        return
    parent = view.get("modifyingViewUid")
    if view.get("viewType") == "tabular" and parent:
        repair = Repair(
            next_step="describe_parent",
            tool="seattle_data_describe_dataset",
            arguments={"dataset_id": parent},
            alternative="This view renders its parent dataset; describe and query the parent.",
        )
    else:
        link = _access_point(view)
        repair = Repair(
            next_step="find_tabular",
            tool="seattle_data_search_datasets",
            arguments={"asset_scope": "tabular"},
            alternative=f"Open this external entry outside this server: {link}"
            if link
            else "External entries can only be opened at their portal URL.",
        )
    raise SeattleDataError(
        ErrorCode.DATASET_NOT_QUERYABLE,
        "This catalog entry is not a queryable dataset.",
        details=ErrorDetails(
            field="dataset_id", value=dataset_id, reason=f"asset_type={asset_type}"
        ),
        repair=repair,
    )


def _access_point(view: dict[str, Any]) -> str | None:
    points = (view.get("metadata") or {}).get("accessPoints") or {}
    return next((str(u) for u in points.values() if str(u).startswith("https://")), None)


def _coverage_columns(dataset_id: str, time_columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidenced = TIME_EVIDENCE.get(dataset_id, {})
    primary = [c for c in time_columns if c["fieldName"] in evidenced]
    return primary or time_columns[:1]


def place_specs(dataset_id: str, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {c.get("fieldName") for c in columns}
    specs: list[dict[str, Any]] = [
        {"column": name, "source": "column", **facts}
        for name, facts in PLACE_FIELDS.get(dataset_id, {}).items()
        if name in names
    ]
    for c in columns:
        region = ((c.get("computationStrategy") or {}).get("parameters") or {}).get("region")
        if not region:
            continue
        layer_id = str(region).lstrip("_")
        known = REGION_LAYERS.get(layer_id)
        specs.append(
            {
                "column": c["fieldName"],
                "source": "computed_region",
                "layer_id": layer_id,
                "kind": known["kind"] if known else "other",
                "reliability": known["reliability"] if known else "partial",
                "reason": (known or {}).get("reason")
                or (None if known else "Region layer not reviewed; check assigned_fraction."),
                "label_column": known["label_column"] if known else None,
                "vintage": known["vintage"] if known else None,
            }
        )
    return specs


async def _count(client: SodaClient, dataset_id: str) -> int:
    rows = await client.soql(dataset_id, {"$select": "count(*) AS n"})
    if len(rows) != 1 or not str(rows[0].get("n", "")).isdigit():
        raise shape_error("Expected one count row.")
    return int(rows[0]["n"])


async def _min_max(client: SodaClient, dataset_id: str, column: str) -> dict[str, Any]:
    rows = await client.soql(dataset_id, {"$select": f"min({column}) AS lo, max({column}) AS hi"})
    if len(rows) != 1:
        raise shape_error("Expected one min/max row.")
    return rows[0]


async def _value_groups(client: SodaClient, dataset_id: str, column: str) -> list[dict[str, Any]]:
    return await client.soql(
        dataset_id,
        {
            "$select": f"{column} AS v, count(*) AS n",
            "$group": column,
            "$order": "n DESC",
            "$limit": _MAX_VALUES + 1,
        },
    )


async def _sample(
    client: SodaClient, dataset_id: str, columns: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    select = ",".join(c["fieldName"] for c in columns)
    return await client.soql(dataset_id, {"$select": select, "$limit": limit})


async def _region_labels(
    client: SodaClient,
    places: list[dict[str, Any]],
    place_stats: dict[str, _Outcome],
) -> dict[str, dict[str, str]]:
    """Map each computed-region column's feature ids to labels, for layers with few used values."""
    labels: dict[str, dict[str, str]] = {}
    with anyio.move_on_after(_LABEL_FETCH_TIMEOUT):
        for spec in places:
            if spec["source"] != "computed_region" or not spec.get("label_column"):
                continue
            groups = place_stats[spec["column"]]
            if groups.status != "ok" or len(groups.value) > _MAX_VALUES:
                continue
            ids = sorted(
                {str(r["v"]) for r in groups.value if str(r.get("v", "")).isdigit()}, key=int
            )
            if not ids:
                continue
            layer = spec["layer_id"]
            names = labels[spec["column"]] = {}
            for i in ids:
                if (label := cache.region_labels.get((layer, i))) is not None:
                    names[i] = label
            missing = [i for i in ids if i not in names]
            if not missing:
                continue
            try:
                rows = await client.soql(
                    layer,
                    {
                        "$select": f"_feature_id AS id, {spec['label_column']} AS label",
                        "$where": f"_feature_id IN ({','.join(missing)})",
                        "$limit": len(missing),
                    },
                )
            except SeattleDataError as exc:
                if exc.envelope.code in _ACTIONABLE:
                    raise
                # Values keep their numeric ids; labels_available says the names are missing.
                continue
            for r in rows:
                feature, label = str(r["id"]), clean(str(r.get("label", "")), limit=100) or ""
                names[feature] = label
                cache.region_labels.put((layer, feature), label)
    return labels


def _row_count(outcome: _Outcome, columns: list[dict[str, Any]]) -> RowCount:
    if outcome.status == "ok":
        return RowCount(value=outcome.value, origin="live_query")
    for c in columns:
        cached = c.get("cachedContents") or {}
        if "non_null" in cached and "null" in cached:
            return RowCount(
                value=int(cached["non_null"]) + int(cached["null"]),
                origin="cached_metadata",
                status=outcome.status,
            )
    return RowCount(status=outcome.status)


def _coverage(meta: dict[str, Any], outcome: _Outcome) -> Coverage:
    column = meta["fieldName"]
    if outcome.status != "ok":
        cached = meta.get("cachedContents") or {}
        lo, hi = _scalar(cached.get("smallest")), _scalar(cached.get("largest"))
        if lo or hi:
            return Coverage(
                column=column, min=lo, max=hi, origin="cached_metadata", status=outcome.status
            )
        return Coverage(column=column, status=outcome.status)
    return Coverage(
        column=column,
        min=outcome.value.get("lo"),
        max=outcome.value.get("hi"),
        origin="live_query",
    )


def _column(c: dict[str, Any], detail: Detail) -> Column:
    if detail != "full":
        return Column(name=c["fieldName"], type=c.get("dataTypeName", "unknown"))
    return Column(
        name=c["fieldName"],
        type=c.get("dataTypeName", "unknown"),
        description=html_to_text(c.get("description"), limit=500),
        stats=_stats(c.get("cachedContents")),
    )


def _stats(cached: dict[str, Any] | None) -> ColumnStats | None:
    if not cached:
        return None
    top = [
        clean(str(t["item"]), limit=100) or ""
        for t in cached.get("top") or []
        if not isinstance(t.get("item"), dict | list)
    ][:5]
    return ColumnStats(
        top=top or None,
        smallest=clean(_scalar(cached.get("smallest")), limit=100),
        largest=clean(_scalar(cached.get("largest")), limit=100),
        null=int(cached["null"]) if str(cached.get("null", "")).isdigit() else None,
    )


def _scalar(value: Any) -> str | None:
    return None if value is None or isinstance(value, dict | list) else str(value)


def _place_field(
    spec: dict[str, Any],
    groups: _Outcome | None,
    labels: dict[str, dict[str, str]],
) -> PlaceField:
    field = PlaceField(
        column=spec["column"],
        kind=spec["kind"],
        source=spec["source"],
        reliability=spec["reliability"],
        reliability_reason=spec.get("reason"),
    )
    if groups is None:
        return field
    field.layer_id = spec.get("layer_id")
    field.label_column = spec.get("label_column")
    field.boundary_vintage = spec.get("vintage")
    if groups.status != "ok":
        field.stats_status = groups.status
        return field
    field.stats_status = "ok"
    placeholders = [
        PlaceholderValue(value=str(r.get("v", "")), rows=int(r["n"]))
        for r in groups.value
        if "v" in r and str(r["v"]) in PLACEHOLDER_VALUES and spec["source"] == "column"
    ]
    nulls = sum(int(r["n"]) for r in groups.value if "v" not in r)
    if nulls:
        placeholders.append(PlaceholderValue(value="null", rows=nulls))
    field.placeholder_values = placeholders or None
    assigned = [
        r
        for r in groups.value
        if "v" in r and not (spec["source"] == "column" and str(r["v"]) in PLACEHOLDER_VALUES)
    ]
    if len(groups.value) <= _MAX_VALUES:
        field.value_count = len(assigned)
        names = labels.get(spec["column"], {})
        if spec["column"] in labels:
            field.labels_available = bool(names)
        field.values = sorted(
            (
                PlaceValue(
                    label=names.get(str(r["v"]), clean(str(r["v"]), limit=100) or ""),
                    value=str(r["v"]),
                )
                for r in assigned
            ),
            key=lambda v: v.label,
        )
        # Exact only when every group came back, and taken from that one snapshot: a row
        # count from another query can disagree with the groups as the data changes.
        total = sum(int(r["n"]) for r in groups.value)
        if total:
            unassigned = sum(p.rows for p in placeholders)
            field.assigned_fraction = round(1 - unassigned / total, 4)
    return field


def _geo_unusable(columns: list[dict[str, Any]]) -> list[GeoUnusable]:
    out: list[GeoUnusable] = []
    for c in columns:
        name = str(c.get("fieldName", ""))
        if not _COORDINATE.search(name):
            continue
        if c.get("dataTypeName") == "text":
            reason = "Text coordinate column (values may be redacted); near cannot use it."
        elif c.get("dataTypeName") == "number":
            reason = (
                "Numeric coordinate; filter with between on latitude and longitude instead of near."
            )
        else:
            continue
        out.append(GeoUnusable(column=name, reason=reason))
    return out


def _time(dataset_id: str, c: dict[str, Any]) -> TimeSemantics:
    evidence = TIME_EVIDENCE.get(dataset_id, {}).get(c["fieldName"], {})
    return TimeSemantics(
        column=c["fieldName"],
        type=c["dataTypeName"],
        timezone=evidence.get("timezone"),
        evidence=evidence.get("evidence"),
    )


def _matches(c: dict[str, Any], needle: str) -> bool:
    needle = needle.lower()
    return (
        needle in str(c.get("fieldName", "")).lower()
        or needle in str(c.get("description") or "").lower()
    )


def _fill(rows: Any, columns: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """SODA2 omits null keys; restore them so every row has every selected column."""
    if not isinstance(rows, list):
        return None
    names = [c["fieldName"] for c in columns]
    return [{name: strip_control(row.get(name)) for name in names} for row in rows]
