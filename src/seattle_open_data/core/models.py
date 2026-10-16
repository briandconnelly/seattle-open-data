"""Success output shapes for the four tools (PLAN §4).

Top-level results are open (`extra="allow"`): the documented extension point for
minor-release additions [9.additive-direction]. Nested objects are closed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Detail = Literal["summary", "full"]
Queryability = Literal["supported", "external", "unsupported", "unknown"]
Origin = Literal["live_query", "cached_metadata"]
Reliability = Literal["ok", "partial", "unreliable"]
PlaceKind = Literal[
    "neighborhood", "precinct", "sector", "beat", "zip", "council_district", "census_tract"
]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Source(Closed):
    request_id: str
    now_local: str
    retrieved_at: str | None = None


# --- search -----------------------------------------------------------------


class DatasetSummary(Closed):
    dataset_id: str
    title: str
    asset_type: str
    queryability: Queryability
    portal_url: str
    updated_at: str | None = None
    guide_uri: str | None = None
    derived_from: str | None = None
    external_url: str | None = None
    category: str | None = None
    description: str | None = None


class SearchResult(Open):
    datasets: list[DatasetSummary]
    has_more: bool
    next_cursor: str | None = None
    source: Source


# --- describe ---------------------------------------------------------------


class RowCount(Closed):
    value: int | None = None
    origin: Origin | None = None
    status: Literal["ok", "timeout", "unavailable"] = "ok"


class Coverage(Closed):
    column: str
    min: str | None = None
    max: str | None = None
    origin: Origin | None = None
    status: Literal["ok", "timeout", "unavailable"] = "ok"


class ColumnStats(Closed):
    top: list[str] | None = None
    smallest: str | None = None
    largest: str | None = None
    null: int | None = None
    origin: Literal["cached_metadata"] = "cached_metadata"


class Column(Closed):
    name: str
    type: str
    description: str | None = None
    stats: ColumnStats | None = None


class PlaceValue(Closed):
    label: str
    value: str


class PlaceholderValue(Closed):
    value: str
    rows: int


class PlaceField(Closed):
    column: str
    kind: PlaceKind
    source: Literal["column", "computed_region"]
    reliability: Reliability
    reliability_reason: str | None = None
    values: list[PlaceValue] | None = None
    placeholder_values: list[PlaceholderValue] | None = None
    assigned_fraction: float | None = None
    layer_id: str | None = None
    label_column: str | None = None
    boundary_vintage: str | None = None
    stats_status: Literal["ok", "timeout"] | None = None


class GeoColumn(Closed):
    column: str
    type: str


class GeoUnusable(Closed):
    column: str
    reason: str


class TimeSemantics(Closed):
    column: str
    type: str
    timezone: str | None = None
    evidence: str | None = None


class ExampleQuery(Closed):
    question: str
    arguments: dict[str, Any]


class DescribeResult(Open):
    dataset_id: str
    title: str
    queryability: Queryability
    row_count: RowCount
    data_coverage: list[Coverage]
    columns: list[Column]
    stats_available: bool
    place_fields: list[PlaceField]
    geo_columns: list[GeoColumn]
    geo_unusable: list[GeoUnusable] | None = None
    time_semantics: list[TimeSemantics]
    guide_uri: str | None = None
    example_queries: list[ExampleQuery] | None = None
    sample_rows: list[dict[str, Any]] | None = None
    source: Source


# --- query ------------------------------------------------------------------


class ResultColumn(Closed):
    name: str
    type: str


class CoverageWarning(Closed):
    column: str
    reliability: Reliability
    reason: str
    assigned_fraction: float | None = None


class QueryResult(Open):
    dataset_id: str
    columns: list[ResultColumn]
    rows: list[dict[str, Any]]
    returned_rows: int
    has_more: bool
    next_cursor: str | None = None
    truncated: bool
    truncation_reason: str | None = None
    truncation_hint: str | None = None
    consistency: Literal["stable", "best_effort", "single_page"]
    coverage_warnings: list[CoverageWarning] | None = None
    query_url: str | None = None
    query_url_omitted: bool | None = None
    download_url: str | None = None
    download_url_omitted: bool | None = None
    effective_query: str | None = None
    source: Source


# --- reference --------------------------------------------------------------


class ReferenceEntry(Closed):
    item_id: str
    title: str
    description: str
    uri: str


class ReferenceResult(Open):
    topic: str
    uri: str
    title: str
    summary: str
    version: str
    entries: list[ReferenceEntry] | None = None
    body: str | None = None
    chunk_id: str | None = None
    next_chunk_id: str | None = None
    source: Source
