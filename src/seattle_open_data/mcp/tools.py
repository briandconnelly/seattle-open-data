"""The four tools (PLAN §4). Handlers raise `SeattleDataError`; `mcp/server.py` turns
every failure, including framework argument validation, into the error envelope."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

# FastMCP resolves return annotations at registration, so this stays a runtime import.
from fastmcp.tools import ToolResult  # noqa: TC002
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from seattle_open_data.constants import (
    DATASET_ID_PATTERN,
    QUERY_PAGE_SIZE_MAX,
    QUERY_TEXT_MAX_BYTES,
    SAMPLE_ROWS_MAX,
    SEARCH_PAGE_SIZE_MAX,
)
from seattle_open_data.core import arguments, catalog, describe, query, reference_service
from seattle_open_data.core.http import get_client
from seattle_open_data.core.models import (
    DescribeResult,
    Detail,
    QueryResult,
    ReferenceResult,
    SearchResult,
)
from seattle_open_data.mcp.result import success
from seattle_open_data.mcp.schema import compact_schema

if TYPE_CHECKING:
    from fastmcp import FastMCP

_READ_ONLY_OPEN = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_READ_ONLY_CLOSED = ToolAnnotations(read_only_hint=True, open_world_hint=False)

DatasetId = Annotated[
    str,
    Field(
        pattern=DATASET_ID_PATTERN,
        description="Socrata dataset id, e.g. kzjm-xkqj (from seattle_data_search_datasets).",
    ),
]
DetailArg = Annotated[
    Detail | None,
    Field(description="summary (default) or full; changes field density, never row count."),
]
Cursor = Annotated[
    str | None,
    Field(min_length=1, description="next_cursor from the previous page; omit for page one."),
]
Text = Annotated[str, Field(min_length=1, max_length=QUERY_TEXT_MAX_BYTES)]


class Near(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: Annotated[
        str, Field(min_length=1, description="A column listed in describe's geo_columns.")
    ]
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    radius_m: Annotated[float, Field(gt=0, le=50_000, description="Radius in meters.")]


async def seattle_data_search_datasets(
    query: Annotated[
        str | None,
        Field(min_length=1, max_length=200, description="Words to match; omit to browse."),
    ] = None,
    asset_scope: Annotated[
        Literal["tabular", "all"] | None,
        Field(
            description="tabular (default): queryable datasets and filter views. "
            "all: adds federated ArcGIS and external links, which cannot be queried."
        ),
    ] = None,
    category: Annotated[
        str | None,
        Field(min_length=1, max_length=100, description="Portal category; omit for all."),
    ] = None,
    page_size: Annotated[
        int | None, Field(ge=1, le=SEARCH_PAGE_SIZE_MAX, description="Default 10.")
    ] = None,
    cursor: Cursor = None,
    detail: DetailArg = None,
) -> ToolResult:
    """Find Seattle datasets by topic. Use first when the dataset id is unknown.

    Results carry queryability: only `supported` datasets can be described or queried.
    Map and chart views are folded into their parent dataset.
    Example: {"query": "fire 911"}. Typically 1-3 s.
    """
    arguments.check_search(
        {
            "query": query,
            "asset_scope": asset_scope,
            "category": category,
            "page_size": page_size,
            "cursor": cursor,
            "detail": detail,
        }
    )
    result = await catalog.search(
        get_client(),
        query=query,
        asset_scope=asset_scope or "tabular",
        category=category,
        page_size=page_size,
        cursor=cursor,
        detail=detail or "summary",
    )
    return success(result)


async def seattle_data_describe_dataset(
    dataset_id: DatasetId,
    column_query: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=100,
            description="Only columns whose name or description contains this; omit for all.",
        ),
    ] = None,
    sample_rows: Annotated[
        int | None,
        Field(ge=0, le=SAMPLE_ROWS_MAX, description="Example rows to include. Default 0."),
    ] = None,
    detail: DetailArg = None,
) -> ToolResult:
    """Decide whether and how to query a dataset: columns, live row count, time coverage,
    place fields for "in <area>" questions (with reliability), and geo columns for `near`.

    Use before querying an unfamiliar dataset, and with detail="full" to get place-field values.
    Example: {"dataset_id": "kzjm-xkqj", "detail": "full"}.
    Typically 1-3 s; up to about 25 s on very large datasets.
    """
    result = await describe.describe(
        get_client(),
        dataset_id=dataset_id,
        column_query=column_query,
        sample_rows=sample_rows or 0,
        detail=detail or "summary",
    )
    return success(result)


async def seattle_data_query_dataset(
    dataset_id: DatasetId,
    mode: Annotated[
        Literal["clauses", "soql"] | None,
        Field(
            description="clauses (default): paged query built from the clause arguments. "
            "soql: run `soql` verbatim, one page."
        ),
    ] = None,
    select: Annotated[
        list[Text] | None,
        Field(
            min_length=1,
            description='Columns or expressions, e.g. ["type", "count(*) AS n"]. '
            "Omit for all data columns.",
        ),
    ] = None,
    where: Annotated[
        str | None,
        Field(min_length=1, max_length=QUERY_TEXT_MAX_BYTES, description="SoQL predicate."),
    ] = None,
    group_by: Annotated[list[Text] | None, Field(min_length=1)] = None,
    having: Annotated[str | None, Field(min_length=1, max_length=QUERY_TEXT_MAX_BYTES)] = None,
    order_by: Annotated[
        list[Text] | None, Field(min_length=1, description='e.g. ["n DESC"].')
    ] = None,
    search_text: Annotated[
        str | None,
        Field(min_length=1, max_length=200, description="Full-text search across text columns."),
    ] = None,
    near: Annotated[
        Near | None, Field(description="Rows within radius_m meters of a point.")
    ] = None,
    soql: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=QUERY_TEXT_MAX_BYTES,
            description="Full SoQL statement; requires mode='soql'.",
        ),
    ] = None,
    page_size: Annotated[
        int | None,
        Field(
            ge=1,
            le=QUERY_PAGE_SIZE_MAX,
            description="Rows per page. Default 100; a continuation keeps the first page's size.",
        ),
    ] = None,
    cursor: Cursor = None,
    detail: DetailArg = None,
) -> ToolResult:
    """Run a bounded read or aggregate on one dataset.

    Prefer aggregates (count, group_by) on large datasets. Area filters are ordinary
    `where` predicates on place fields from seattle_data_describe_dataset.
    Without order_by, rows come in a stable but arbitrary order.
    With cursor, send only dataset_id, cursor, and optionally page_size or detail.
    Example: {"dataset_id": "kzjm-xkqj", "select": ["type", "count(*) AS n"],
    "group_by": ["type"], "order_by": ["n DESC"]}.
    Typically 1-5 s; fails with query_timeout after 30 s.
    """
    arguments.check_query(locals())
    result = await query.run(
        get_client(),
        dataset_id=dataset_id,
        mode=mode or "clauses",
        clauses={
            "select": select,
            "where": where,
            "group_by": group_by,
            "having": having,
            "order_by": order_by,
            "search_text": search_text,
            "near": near.model_dump() if near else None,
        },
        soql=soql,
        page_size=page_size,
        cursor=cursor,
        detail=detail or "summary",
    )
    return success(result)


async def seattle_data_get_reference(
    topic: Annotated[
        Literal["index", "capabilities", "guides", "soql", "errors"] | None,
        Field(description="Default index."),
    ] = None,
    item_id: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=64,
            description="An item from the topic's entries; omit for the topic index.",
        ),
    ] = None,
    chunk_id: Annotated[
        str | None, Field(min_length=1, max_length=64, description="Requires item_id.")
    ] = None,
    detail: Annotated[
        Detail | None,
        Field(
            description="Accepted for symmetry with the other tools; "
            "reference bodies have one density."
        ),
    ] = None,
) -> ToolResult:
    """Read the server's reference: capability summary, SoQL help, dataset guides, and the
    full error catalog. Call with no arguments for the index.

    Example: {"topic": "soql", "item_id": "dates"}.
    """
    arguments.check_reference(locals())
    del detail  # Reference bodies have one density.
    result = reference_service.read(topic or "index", item_id, chunk_id)
    return success(result)


_TOOLS: tuple[tuple[Any, type[BaseModel], ToolAnnotations], ...] = (
    (seattle_data_search_datasets, SearchResult, _READ_ONLY_OPEN),
    (seattle_data_describe_dataset, DescribeResult, _READ_ONLY_OPEN),
    (seattle_data_query_dataset, QueryResult, _READ_ONLY_OPEN),
    (seattle_data_get_reference, ReferenceResult, _READ_ONLY_CLOSED),
)


def register_tools(mcp: FastMCP) -> None:
    for fn, output_model, hints in _TOOLS:
        tool = FunctionTool.from_function(
            fn,
            annotations=hints,
            output_schema=compact_schema(output_model.model_json_schema()),
        )
        tool.parameters = compact_schema(tool.parameters)
        mcp.add_tool(tool)
