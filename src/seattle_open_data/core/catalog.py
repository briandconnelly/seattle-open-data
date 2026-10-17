"""Catalog search over the Socrata Discovery API (docs/design.md: tool 1)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

import anyio

from seattle_open_data.constants import (
    CATALOG_DOMAIN,
    DATASET_ID_PATTERN,
    DISCOVERY_API,
    PUBLIC_HOST,
    SEARCH_EXTERNAL_LIMIT,
    SEARCH_EXTERNAL_TIMEOUT_SECONDS,
)
from seattle_open_data.core import cursor as cursors
from seattle_open_data.core.context import make_source
from seattle_open_data.core.errors import Repair, SeattleDataError
from seattle_open_data.core.guides import BY_DATASET
from seattle_open_data.core.http import shape_error
from seattle_open_data.core.models import (
    DatasetSummary,
    Detail,
    MatchEvidence,
    MatchField,
    Queryability,
    SearchResult,
)
from seattle_open_data.core.reference import topic_uri
from seattle_open_data.core.text import clean, html_to_text

if TYPE_CHECKING:
    from seattle_open_data.core.http import SodaClient

logger = logging.getLogger(__name__)

AssetScope = Literal["tabular", "all"]

_SUPPORTED = frozenset({"dataset", "filter"})
_EXTERNAL = frozenset({"federated_href", "href"})
#: Views that render a parent dataset; search folds them into that parent.
_FOLDED = frozenset({"map", "chart", "visualization", "calendar", "form"})

_DEFAULT_PAGE_SIZE = 10
#: Bounds cursor size; a duplicate can reappear only after this many results.
_SEEN_CAP = 200
_TOOL = "seattle_data_search_datasets"
#: `{}` would browse the whole catalog, not restart this search (#16).
_RESTART = Repair(
    next_step="read_paging",
    tool="seattle_data_get_reference",
    arguments={"topic": "soql", "item_id": "paging"},
    alternative="Resend the first-page search with all its original arguments, without cursor.",
)
#: Asks the catalog for federated and external links only (probed 2026-10-16).
_EXTERNAL_ONLY = "federated_href,href"

#: Grammatical words only; domain synonyms are deliberately out of scope (docs/decisions.md).
_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)
#: Underscores separate words too, so API field names like `trees_planted` match.
_WORD = re.compile(r"[^\W_]+")


def _catalog_body(body: Any) -> dict[str, Any]:
    """An unexpected catalog shape must not read as "no datasets match"."""
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("results"), list)
        or not str(body.get("resultSetSize", "")).isdigit()
    ):
        raise shape_error("Expected a Discovery API result object.")
    if not all(_well_formed(item) for item in body["results"]):
        raise shape_error("Expected catalog entries with text and list metadata.")
    return body


#: Entry fields read as lists or text; another type would surface as an `internal_error`.
_LISTS = {
    "resource": ("parent_fxf", "columns_name", "columns_field_name"),
    "classification": ("domain_tags", "tags"),
}


def _well_formed(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    for part, keys in _LISTS.items():
        section = item.get(part)
        if section is None:
            continue
        if not isinstance(section, dict):
            return False
        if any(not isinstance(section.get(k), list | None) for k in keys):
            return False
    return isinstance((item.get("resource") or {}).get("description"), str | None)


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
    first_page = cursor is None
    if cursor is not None:
        state = cursors.decode(cursor, "search", restart=_RESTART)
        query, asset_scope, category = state["q"], state["scope"], state["cat"]
        offset = int(state["off"])
        page_size = page_size or int(state.get("ps", _DEFAULT_PAGE_SIZE))
        earlier = [str(i) for i in state.get("seen", [])]
    else:
        offset = 0
        earlier = []
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

    # A tabular search hides federated assets; look for them alongside, never instead.
    look_external = first_page and bool(query) and asset_scope == "tabular"
    primary, external = _Outcome(), _Outcome()
    async with anyio.create_task_group() as tg:
        if look_external:
            tg.start_soon(_external, client, params, detail, external)
        tg.start_soon(_primary, client, params, primary, tg.cancel_scope)
    if primary.error is not None:
        raise primary.error
    body = primary.body
    results = body["results"]
    total = int(body["resultSetSize"])

    folded = await _fold(client, results, detail, already=set(earlier))
    datasets = [summary for summary, _, _ in folded]
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
                # Folded map views can repeat a dataset from an earlier page; remember recent ids.
                "seen": [*earlier, *(d.dataset_id for d in datasets)][-_SEEN_CAP:],
            },
        )
        if has_more
        else None
    )
    result = SearchResult(
        datasets=datasets,
        has_more=has_more,
        next_cursor=next_cursor,
        source=make_source(retrieved=True),
    )
    if query:
        _attach_discovery(
            result,
            folded,
            external if look_external else None,
            query=query,
            asset_scope=asset_scope,
            category=category,
            first_page=first_page,
        )
    return result


def _attach_discovery(
    result: SearchResult,
    folded: list[tuple[DatasetSummary, dict[str, Any], str | None]],
    external: _Outcome | None,
    *,
    query: str,
    asset_scope: AssetScope,
    category: str | None,
    first_page: bool,
) -> None:
    """Match evidence, external leads, and the no-match suggestion (docs/design.md: tool 1)."""
    terms = _terms(query)
    result.match_evidence = [
        _evidence(summary.dataset_id, item, via, terms) for summary, item, via in folded
    ]
    if external is not None:
        if external.error is None:
            result.external_search_status = "completed"
            result.external_matches = external.body
        else:
            result.external_search_status = "unavailable"
    if first_page and terms and not any(e.matched_terms for e in result.match_evidence):
        result.suggestion = _suggest(query, asset_scope, category)


@dataclass
class _Outcome:
    body: Any = None
    error: Exception | None = None


async def _primary(
    client: SodaClient, params: dict[str, Any], out: _Outcome, scope: anyio.CancelScope
) -> None:
    try:
        out.body = _catalog_body(await client.get_json(DISCOVERY_API, params))
    except SeattleDataError as exc:
        out.error = exc
        scope.cancel()  # the external lookup is worthless without the primary result


async def _external(
    client: SodaClient, primary: dict[str, Any], detail: Detail, out: _Outcome
) -> None:
    """Optional enrichment: any failure, parsing included, leaves the search itself intact.

    Cancellation is not an `Exception`, so a cancelled lookup still unwinds.
    """
    params = {**primary, "only": _EXTERNAL_ONLY, "limit": SEARCH_EXTERNAL_LIMIT, "offset": 0}
    try:
        body = _catalog_body(
            await client.get_json(DISCOVERY_API, params, timeout=SEARCH_EXTERNAL_TIMEOUT_SECONDS)
        )
        leads = [_summarize(item, detail) for item in body["results"][:SEARCH_EXTERNAL_LIMIT]]
        if not all(_is_lead(lead) for lead in leads):
            raise shape_error("Expected only external catalog assets with a link.")
        out.body = leads
    except SeattleDataError as exc:
        out.error = exc
    except Exception as exc:
        logger.exception("external-asset lookup failed unexpectedly")
        out.error = exc


def _is_lead(summary: DatasetSummary) -> bool:
    return (
        re.fullmatch(DATASET_ID_PATTERN, summary.dataset_id) is not None
        and summary.queryability == "external"
        and _is_web_url(summary.external_url)
    )


def _is_web_url(url: str | None) -> bool:
    parts = urlsplit(url or "")
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _suggest(query: str, asset_scope: AssetScope, category: str | None) -> Repair:
    """One alternative search when nothing visibly matched; not a fix for a vocabulary gap."""
    if category:
        arguments: dict[str, Any] = {"query": query}
        if asset_scope != "tabular":
            arguments["asset_scope"] = asset_scope
        return Repair(
            next_step="drop_category",
            tool=_TOOL,
            arguments=arguments,
            alternative="The same search without the category filter. Results still need "
            "checking against the question.",
        )
    return Repair(
        next_step="browse",
        tool=_TOOL,
        arguments={},
        alternative="No result's catalog metadata contains a query word. Browse the most-viewed "
        "datasets, or search again with the publisher's likely terms; if nothing fits, "
        "say Seattle may not publish this data.",
    )


def _terms(query: str) -> list[str]:
    words = (w.casefold() for w in _WORD.findall(query))
    return list(dict.fromkeys(w for w in words if w not in _STOPWORDS))


def _variants(word: str) -> set[str]:
    """A word matches its plural or singular: a trailing "s" only, no stemming.

    Two-letter words ("us", "as") keep their "s"; three-letter plurals ("ids", "rvs") lose it.
    """
    return {word, word[:-1]} if word.endswith("s") and len(word) > 2 else {word, word + "s"}


def _evidence(
    dataset_id: str, item: dict[str, Any], via: str | None, terms: list[str]
) -> MatchEvidence:
    resource = item.get("resource") or {}
    classification = item.get("classification") or {}
    texts: dict[MatchField, str] = {
        "title": str(resource.get("name") or ""),
        "tags": " ".join(
            str(t)
            for t in [
                *(classification.get("domain_tags") or []),
                *(classification.get("tags") or []),
            ]
        ),
        "columns": " ".join(
            str(c)
            for c in [
                *(resource.get("columns_name") or []),
                *(resource.get("columns_field_name") or []),
            ]
        ),
        "description": html_to_text(resource.get("description")) or "",
    }
    words = {field: {w.casefold() for w in _WORD.findall(text)} for field, text in texts.items()}
    matched_fields = [f for f in texts if any(_variants(t) & words[f] for t in terms)]
    matched = [t for t in terms if any(_variants(t) & words[f] for f in texts)]
    return MatchEvidence(
        dataset_id=dataset_id,
        matched_terms=matched,
        unmatched_terms=[t for t in terms if t not in matched],
        matched_fields=matched_fields,
        matched_via=via,
    )


def _access_point(item: dict[str, Any]) -> str | None:
    points = (item.get("metadata") or {}).get("access_points") or {}
    url = next((str(u) for u in points.values() if str(u).startswith("https://")), None)
    return url or public_url(item.get("link"))


async def _fold(
    client: SodaClient, results: list[Any], detail: Detail, *, already: set[str]
) -> list[tuple[DatasetSummary, dict[str, Any], str | None]]:
    """Replace each map or chart match with its parent dataset, once, at its first rank.

    `already` holds datasets emitted on earlier pages, which are not repeated. Each entry
    keeps the catalog item that matched and, for a folded view, that view's id.
    """
    slots: list[tuple[DatasetSummary | str, dict[str, Any]]] = []
    for item in results:
        resource = item.get("resource") or {}
        if str(resource.get("type")) in _FOLDED:
            parents = resource.get("parent_fxf") or []
            if parents:
                slots.append((str(parents[0]), item))
        else:
            slots.append((_summarize(item, detail), item))
    # Seeding with this page's summaries lets a map ranked above its own parent keep that rank.
    parents: dict[str, DatasetSummary] = {
        s.dataset_id: s for s, _ in slots if isinstance(s, DatasetSummary)
    }
    missing = sorted(
        {s for s, _ in slots if isinstance(s, str) and s not in parents and s not in already}
    )
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
    out: list[tuple[DatasetSummary, dict[str, Any], str | None]] = []
    seen = set(already)
    for slot, item in slots:
        summary = parents.get(slot) if isinstance(slot, str) else slot
        if summary is not None and summary.dataset_id not in seen:
            seen.add(summary.dataset_id)
            via = str((item.get("resource") or {}).get("id")) if isinstance(slot, str) else None
            out.append((summary, item, via))
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
