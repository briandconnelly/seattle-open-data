"""Catalog search over the Socrata Discovery API (PLAN §4, tool 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from seattle_open_data.constants import (
    CATALOG_DOMAIN,
    DISCOVERY_API,
    PUBLIC_HOST,
)
from seattle_open_data.core import cursor as cursors
from seattle_open_data.core.context import make_source
from seattle_open_data.core.errors import Repair
from seattle_open_data.core.guides import BY_DATASET
from seattle_open_data.core.http import shape_error
from seattle_open_data.core.models import DatasetSummary, Detail, Queryability, SearchResult
from seattle_open_data.core.reference import topic_uri
from seattle_open_data.core.text import clean, html_to_text

if TYPE_CHECKING:
    from seattle_open_data.core.http import SodaClient

AssetScope = Literal["tabular", "all"]

_SUPPORTED = frozenset({"dataset", "filter"})
_EXTERNAL = frozenset({"federated_href", "href"})
#: Views that render a parent dataset; search folds them into that parent.
_FOLDED = frozenset({"map", "chart", "visualization", "calendar", "form"})

_DEFAULT_PAGE_SIZE = 10
_RESTART = Repair(next_step="restart_search", tool="seattle_data_search_datasets", arguments={})


def _catalog_body(body: Any) -> dict[str, Any]:
    """An unexpected catalog shape must not read as "no datasets match"."""
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("results"), list)
        or not str(body.get("resultSetSize", "")).isdigit()
    ):
        raise shape_error("Expected a Discovery API result object.")
    return body


def queryability(asset_type: str) -> Queryability:
    if asset_type in _SUPPORTED:
        return "supported"
    if asset_type in _EXTERNAL:
        return "external"
    if asset_type in {"file", "story", "measure"}:
        return "unsupported"
    return "unknown"


def public_url(url: str | None) -> str | None:
    """Catalog links can use the alias host; agents should see data.seattle.gov."""
    if not url:
        return None
    parts = urlsplit(url)
    if parts.netloc == CATALOG_DOMAIN:
        parts = parts._replace(netloc=PUBLIC_HOST)
    return urlunsplit(parts)


async def search(
    client: SodaClient,
    *,
    query: str | None,
    asset_scope: AssetScope,
    category: str | None,
    page_size: int | None,
    cursor: str | None,
    detail: Detail,
) -> SearchResult:
    if cursor is not None:
        state = cursors.decode(cursor, "search", restart=_RESTART)
        query, asset_scope, category = state["q"], state["scope"], state["cat"]
        offset = int(state["off"])
        page_size = page_size or int(state.get("ps", _DEFAULT_PAGE_SIZE))
    else:
        offset = 0
    page_size = page_size or _DEFAULT_PAGE_SIZE

    params: dict[str, Any] = {
        "domains": CATALOG_DOMAIN,
        "search_context": PUBLIC_HOST,
        "limit": page_size,
        "offset": offset,
    }
    if query:
        params["q"] = query
    else:
        params["order"] = "page_views_total"
    if category:
        params["categories"] = category
    if asset_scope == "tabular":
        # Maps and charts are requested only to be folded into their parent datasets.
        params["only"] = "dataset,filter,map,chart"

    body = _catalog_body(await client.get_json(DISCOVERY_API, params))
    results = body["results"]
    total = int(body["resultSetSize"])

    datasets = await _fold(client, results, detail)
    next_offset = offset + len(results)
    has_more = bool(results) and next_offset < total
    next_cursor = (
        cursors.encode(
            "search",
            {
                "q": query,
                "scope": asset_scope,
                "cat": category,
                "off": next_offset,
                "ps": page_size,
            },
        )
        if has_more
        else None
    )
    return SearchResult(
        datasets=datasets,
        has_more=has_more,
        next_cursor=next_cursor,
        source=make_source(retrieved=True),
    )


def _access_point(item: dict[str, Any]) -> str | None:
    points = (item.get("metadata") or {}).get("access_points") or {}
    url = next((str(u) for u in points.values() if str(u).startswith("https://")), None)
    return url or public_url(item.get("link"))


async def _fold(client: SodaClient, results: list[Any], detail: Detail) -> list[DatasetSummary]:
    """Replace each map or chart match with its parent dataset, once, in rank order."""
    slots: list[DatasetSummary | str] = []
    for item in results:
        resource = item.get("resource") or {}
        if str(resource.get("type")) in _FOLDED:
            parents = resource.get("parent_fxf") or []
            if parents:
                slots.append(str(parents[0]))
        else:
            slots.append(_summarize(item, detail))
    present = {s.dataset_id for s in slots if isinstance(s, DatasetSummary)}
    missing = sorted({s for s in slots if isinstance(s, str) and s not in present})
    parents: dict[str, DatasetSummary] = {}
    if missing:
        body = await client.get_json(
            DISCOVERY_API,
            {
                "domains": CATALOG_DOMAIN,
                "search_context": PUBLIC_HOST,
                "ids": missing,
                "limit": len(missing),
            },
        )
        for item in _catalog_body(body)["results"]:
            summary = _summarize(item, detail)
            parents[summary.dataset_id] = summary
    out: list[DatasetSummary] = []
    seen: set[str] = set()
    for slot in slots:
        summary = parents.get(slot) if isinstance(slot, str) else slot
        if summary is not None and summary.dataset_id not in seen:
            seen.add(summary.dataset_id)
            out.append(summary)
    return out


def _guide_uri(dataset_id: str, parents: list[str]) -> str | None:
    guide = BY_DATASET.get(dataset_id) or next(
        (BY_DATASET[p] for p in parents if p in BY_DATASET), None
    )
    return topic_uri("guides", guide.guide_id) if guide else None


def _summarize(item: dict[str, Any], detail: Detail) -> DatasetSummary:
    resource = item.get("resource") or {}
    asset_type = str(resource.get("type") or "unknown")
    dataset_id = str(resource.get("id", ""))
    reach = queryability(asset_type)
    parents = resource.get("parent_fxf") or []
    classification = item.get("classification") or {}
    return DatasetSummary(
        dataset_id=dataset_id,
        title=clean(resource.get("name"), limit=200) or dataset_id,
        asset_type=asset_type,
        queryability=reach,
        portal_url=public_url(item.get("permalink")) or f"https://{PUBLIC_HOST}/d/{dataset_id}",
        updated_at=resource.get("data_updated_at") or resource.get("updatedAt"),
        derived_from=parents[0] if asset_type == "filter" and parents else None,
        guide_uri=_guide_uri(dataset_id, parents),
        external_url=_access_point(item) if reach == "external" else None,
        category=clean(classification.get("domain_category")),
        description=html_to_text(resource.get("description"), limit=1000)
        if detail == "full"
        else None,
    )
