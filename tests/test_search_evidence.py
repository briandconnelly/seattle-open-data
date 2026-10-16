"""Search match evidence, external leads, and the no-match suggestion (docs/design.md: tool 1)."""

from __future__ import annotations

from typing import Any

import anyio
import httpx
import jsonschema
import pytest

from seattle_open_data.core.http import SodaClient, set_client
from tests.conftest import wire
from tests.test_repairs import assert_repair_callable

TOOL = "seattle_data_search_datasets"


def item(
    dataset_id: str,
    name: str,
    kind: str = "dataset",
    *,
    tags: list[str] | None = None,
    columns: list[str] | None = None,
    description: str | None = None,
    parent: str | None = None,
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "id": dataset_id,
        "name": name,
        "type": kind,
        "columns_name": columns or [],
        "columns_field_name": [],
        "description": description,
    }
    if parent:
        resource["parent_fxf"] = [parent]
    return {
        "resource": resource,
        "classification": {"domain_tags": tags or [], "tags": []},
        "permalink": f"https://data.seattle.gov/d/{dataset_id}",
        "metadata": {"access_points": {"arcgis": f"https://arcgis.example/{dataset_id}"}},
    }


def body(results: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {"results": results, "resultSetSize": len(results) if total is None else total}


class Catalog:
    """A fake Discovery API that routes the primary, external-only, and parent-id requests."""

    def __init__(self, primary, external=None, parents=None, total=None):
        self.primary, self.external, self.parents, self.total = primary, external, parents, total
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params.multi_items())
        self.sent.append(params)
        if "ids" in params:
            return httpx.Response(200, json=body(self.parents or []))
        if params.get("only") == "federated_href,href":
            if isinstance(self.external, Exception):
                raise self.external
            if isinstance(self.external, httpx.Response):
                return self.external
            return httpx.Response(200, json=body(self.external or []))
        return httpx.Response(200, json=body(self.primary, self.total))

    @property
    def external_requests(self) -> list[dict[str, Any]]:
        return [p for p in self.sent if p.get("only") == "federated_href,href"]


def install(fake: Catalog) -> Catalog:
    set_client(SodaClient(transport=httpx.MockTransport(fake)))
    return fake


async def search(client, tools_list, args: dict[str, Any]) -> dict[str, Any]:
    result = wire(await client.call_tool_mcp(TOOL, args))
    assert result["isError"] is False, result["structuredContent"]
    schema = next(t["outputSchema"] for t in tools_list["tools"] if t["name"] == TOOL)
    jsonschema.validate(result["structuredContent"], schema)
    return result["structuredContent"]


def evidence(out: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    return next(e for e in out["match_evidence"] if e["dataset_id"] == dataset_id)


# --- match evidence ------------------------------------------------------------


async def test_evidence_reports_matched_and_unmatched_terms(client, tools_list):
    install(
        Catalog(
            [
                item("aaaa-aaaa", "Bridging The Gap", description="<p>Street paving</p>"),
                item("bbbb-bbbb", "SDOT Street Trees", tags=["urban forest"]),
            ]
        )
    )
    out = await search(client, tools_list, {"query": "Street trees"})
    weak = evidence(out, "aaaa-aaaa")
    assert weak["matched_terms"] == ["street"]
    assert weak["unmatched_terms"] == ["trees"]
    assert weak["matched_fields"] == ["description"]
    strong = evidence(out, "bbbb-bbbb")
    assert strong["matched_terms"] == ["street", "trees"]
    assert strong["unmatched_terms"] == []
    assert strong["matched_fields"] == ["title"]


async def test_evidence_lists_every_field_in_a_fixed_order(client, tools_list):
    install(
        Catalog(
            [
                item(
                    "aaaa-aaaa",
                    "Call Data",
                    tags=["911"],
                    columns=["Call Type"],
                    description="Police calls",
                )
            ]
        )
    )
    out = await search(client, tools_list, {"query": "911 calls"})
    assert evidence(out, "aaaa-aaaa")["matched_fields"] == [
        "title",
        "tags",
        "columns",
        "description",
    ]


async def test_evidence_ignores_a_trailing_s_and_common_words(client, tools_list):
    install(Catalog([item("aaaa-aaaa", "Permit Fees", tags=["building permits"])]))
    out = await search(client, tools_list, {"query": "the permits for a building"})
    found = evidence(out, "aaaa-aaaa")
    assert found["matched_terms"] == ["permits", "building"]
    assert found["matched_fields"] == ["title", "tags"]


async def test_evidence_splits_api_field_names_on_underscores(client, tools_list):
    install(Catalog([item("aaaa-aaaa", "Levy Accomplishments", columns=["trees_planted"])]))
    out = await search(client, tools_list, {"query": "trees"})
    assert evidence(out, "aaaa-aaaa")["matched_fields"] == ["columns"]


async def test_a_query_of_only_common_words_gets_no_suggestion(client, tools_list):
    install(Catalog([item("aaaa-aaaa", "Anything")], external=[]))
    out = await search(client, tools_list, {"query": "the of"})
    assert evidence(out, "aaaa-aaaa")["unmatched_terms"] == []
    assert "suggestion" not in out


async def test_evidence_with_no_overlap_is_explicit(client, tools_list):
    install(Catalog([item("5mzj-4rtf", "Public Life Data - People Staying")]))
    out = await search(client, tools_list, {"query": "potholes"})
    assert evidence(out, "5mzj-4rtf") == {
        "dataset_id": "5mzj-4rtf",
        "matched_terms": [],
        "unmatched_terms": ["potholes"],
        "matched_fields": [],
    }


async def test_evidence_of_a_folded_map_comes_from_the_map(client, tools_list):
    install(
        Catalog(
            [item("mmmm-mmmm", "Tree canopy map", "map", parent="aaaa-aaaa")],
            parents=[item("aaaa-aaaa", "Urban Forest Inventory")],
        )
    )
    out = await search(client, tools_list, {"query": "tree canopy"})
    assert [d["dataset_id"] for d in out["datasets"]] == ["aaaa-aaaa"]
    found = evidence(out, "aaaa-aaaa")
    assert found["matched_terms"] == ["tree", "canopy"]
    assert found["matched_via"] == "mmmm-mmmm"


async def test_browse_without_query_has_no_evidence_and_no_external_call(client, tools_list):
    fake = install(Catalog([item("aaaa-aaaa", "Anything")]))
    out = await search(client, tools_list, {})
    assert "match_evidence" not in out
    assert "external_matches" not in out
    assert "external_search_status" not in out
    assert "suggestion" not in out
    assert fake.external_requests == []


# --- external leads --------------------------------------------------------------


async def test_tabular_search_returns_external_leads(client, tools_list):
    fake = install(
        Catalog(
            [item("aaaa-aaaa", "Bridging The Gap")],
            external=[
                item("jyjy-n3ap", "SDOT GIS Datasets", "href"),
                item("gapw-b2rf", "SDOT Trees", "federated_href"),
                item("cccc-cccc", "Three", "federated_href"),
                item("dddd-dddd", "Four", "federated_href"),
            ],
        )
    )
    out = await search(client, tools_list, {"query": "street trees", "category": "Transportation"})
    assert out["external_search_status"] == "completed"
    leads = out["external_matches"]
    assert [d["dataset_id"] for d in leads] == ["jyjy-n3ap", "gapw-b2rf", "cccc-cccc"]
    assert all(d["queryability"] == "external" for d in leads)
    assert all(d["external_url"].startswith("https://") for d in leads)
    [sent] = fake.external_requests
    assert sent["q"] == "street trees"
    assert sent["categories"] == "Transportation", "the caller's category still applies"
    assert sent["limit"] == "3"


async def test_no_external_leads_is_an_empty_completed_list(client, tools_list):
    install(Catalog([item("kzjm-xkqj", "Seattle Real Time Fire 911 Calls")], external=[]))
    out = await search(client, tools_list, {"query": "fire 911"})
    assert out["external_search_status"] == "completed"
    assert out["external_matches"] == []


async def test_all_scope_already_includes_external_assets(client, tools_list):
    fake = install(Catalog([item("gapw-b2rf", "SDOT Trees", "federated_href")]))
    out = await search(client, tools_list, {"query": "trees", "asset_scope": "all"})
    assert fake.external_requests == []
    assert "external_matches" not in out
    assert "external_search_status" not in out
    assert evidence(out, "gapw-b2rf")["matched_terms"] == ["trees"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("simulated"),
        httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "5"}),
        httpx.Response(500, json={"error": True}),
        httpx.Response(200, json={"results": "nope"}),
    ],
    ids=["timeout", "429", "500", "malformed"],
)
async def test_external_failure_keeps_the_primary_result(client, tools_list, failure):
    install(Catalog([item("aaaa-aaaa", "Street Trees")], external=failure))
    out = await search(client, tools_list, {"query": "street trees"})
    assert [d["dataset_id"] for d in out["datasets"]] == ["aaaa-aaaa"]
    assert out["external_search_status"] == "unavailable"
    assert "external_matches" not in out


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(200, json={"results": [None], "resultSetSize": 1}), RuntimeError("bug")],
    ids=["null-entry", "unexpected-exception"],
)
async def test_any_external_failure_keeps_the_primary_result(client, tools_list, failure):
    install(Catalog([item("aaaa-aaaa", "Street Trees")], external=failure))
    out = await search(client, tools_list, {"query": "street trees"})
    assert [d["dataset_id"] for d in out["datasets"]] == ["aaaa-aaaa"]
    assert out["external_search_status"] == "unavailable"


async def test_primary_failure_is_still_an_error(client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("only") == "federated_href,href":
            return httpx.Response(200, json=body([]))
        return httpx.Response(200, json={"results": "nope"})

    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    result = wire(await client.call_tool_mcp(TOOL, {"query": "fire"}))
    assert result["structuredContent"]["code"] == "upstream_unavailable"


async def test_primary_failure_cancels_a_slow_external_lookup(client):
    external_finished = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal external_finished
        if request.url.params.get("only") == "federated_href,href":
            await anyio.sleep(3)  # under the 5 s external timeout: only cancellation ends it early
            external_finished = True
            return httpx.Response(200, json=body([]))
        return httpx.Response(200, json={"results": "nope"})

    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    with anyio.fail_after(1):
        result = wire(await client.call_tool_mcp(TOOL, {"query": "fire"}))
    assert result["structuredContent"]["code"] == "upstream_unavailable"
    assert not external_finished


# --- continuation pages ----------------------------------------------------------


async def test_continuation_has_page_evidence_but_no_leads_or_suggestion(client, tools_list):
    pages = {"0": [item("aaaa-aaaa", "Trees")], "1": [item("bbbb-bbbb", "Unrelated")]}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params.multi_items())
        if params.get("only") == "federated_href,href":
            sent.append(params)
            return httpx.Response(200, json=body([]))
        return httpx.Response(200, json=body(pages[params["offset"]], total=2))

    sent: list[dict[str, Any]] = []
    set_client(SodaClient(transport=httpx.MockTransport(handler)))
    first = await search(client, tools_list, {"query": "trees", "page_size": 1})
    second = await search(client, tools_list, {"cursor": first["next_cursor"]})
    assert evidence(second, "bbbb-bbbb")["unmatched_terms"] == ["trees"]
    assert "external_matches" not in second
    assert "external_search_status" not in second
    assert "suggestion" not in second
    assert len(sent) == 1, "only the first page looks for external leads"


# --- suggestion ------------------------------------------------------------------


async def test_no_overlap_suggests_a_browse(client, tools_list, input_schemas):
    install(Catalog([item("5mzj-4rtf", "Public Life Data - People Staying")], external=[]))
    out = await search(client, tools_list, {"query": "potholes"})
    suggestion = out["suggestion"]
    assert suggestion["tool"] == TOOL
    assert suggestion["arguments"] == {}
    assert suggestion["alternative"]
    assert_repair_callable(suggestion, input_schemas)


async def test_empty_result_in_a_category_suggests_dropping_it(client, tools_list, input_schemas):
    install(Catalog([], external=[]))
    out = await search(
        client,
        tools_list,
        {"query": "car break-ins", "category": "Public Safety", "asset_scope": "all"},
    )
    assert out["datasets"] == []
    assert out["suggestion"]["arguments"] == {"query": "car break-ins", "asset_scope": "all"}
    assert_repair_callable(out["suggestion"], input_schemas)


async def test_any_visible_overlap_means_no_suggestion(client, tools_list):
    install(
        Catalog([item("aaaa-aaaa", "Unrelated"), item("bbbb-bbbb", "Street paving")], external=[])
    )
    out = await search(client, tools_list, {"query": "street trees"})
    assert "suggestion" not in out
