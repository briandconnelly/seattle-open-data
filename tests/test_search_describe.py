"""Milestone 2: catalog search and describe against recorded data.seattle.gov responses.

Re-record with `RECORD_FIXTURES=1 uv run pytest tests/test_search_describe.py`.
"""

from __future__ import annotations

import jsonschema
import pytest

from tests.conftest import wire


async def call(client, tool, args):
    return wire(await client.call_tool_mcp(tool, args))


def ok(result, tools_list, tool):
    assert result["isError"] is False, result["structuredContent"]
    schema = next(t["outputSchema"] for t in tools_list["tools"] if t["name"] == tool)
    jsonschema.validate(result["structuredContent"], schema)
    return result["structuredContent"]


# --- search ------------------------------------------------------------------


@pytest.mark.soda_fixture("search_fire")
async def test_search_uses_catalog_alias_and_tabular_scope(client, tools_list, soda):
    out = ok(
        await call(client, "seattle_data_search_datasets", {"query": "fire 911", "page_size": 3}),
        tools_list,
        "seattle_data_search_datasets",
    )
    fire = next(d for d in out["datasets"] if d["dataset_id"] == "kzjm-xkqj")
    assert fire["queryability"] == "supported"
    assert fire["portal_url"].startswith("https://data.seattle.gov/")
    assert "description" not in fire, "descriptions are full-detail only"
    if hasattr(soda, "requests"):
        # The external-asset lookup runs concurrently, so the primary request is not always first.
        primary = [r for r in soda.requests if "dataset,filter" in r]
        assert len(primary) == 1 and "cos-data.seattle.gov" in primary[0]


@pytest.mark.soda_fixture("search_all_pages")
async def test_search_all_scope_pages_and_marks_external(client, tools_list, soda):
    tool = "seattle_data_search_datasets"
    first = ok(
        await call(client, tool, {"query": "neighborhood", "asset_scope": "all", "page_size": 4}),
        tools_list,
        tool,
    )
    external = [d for d in first["datasets"] if d["queryability"] == "external"]
    assert external, "federated ArcGIS entries appear in scope=all"
    assert all(d["external_url"].startswith("https://") for d in external)
    assert all(d["asset_type"] not in ("map", "chart") for d in first["datasets"])
    assert first["has_more"] is True and first["next_cursor"]

    second = ok(await call(client, tool, {"cursor": first["next_cursor"]}), tools_list, tool)
    assert {d["dataset_id"] for d in second["datasets"]}.isdisjoint(
        d["dataset_id"] for d in first["datasets"]
    )


async def test_search_rejects_tampered_cursor(client):
    result = await call(client, "seattle_data_search_datasets", {"cursor": "eyJ4IjoxfQ.AAAA"})
    envelope = result["structuredContent"]
    assert result["isError"] is True
    assert envelope["code"] == "cursor_expired"
    assert envelope["repair"] == {
        "next_step": "restart_search",
        "tool": "seattle_data_search_datasets",
        "arguments": {},
    }


async def test_search_rejects_malformed_cursor(client):
    result = await call(client, "seattle_data_search_datasets", {"cursor": "not-a-cursor"})
    assert result["structuredContent"]["code"] == "invalid_cursor"


# --- describe ----------------------------------------------------------------


@pytest.mark.soda_fixture("describe_fire_summary")
async def test_describe_summary(client, tools_list, soda):
    out = ok(
        await call(client, "seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"}),
        tools_list,
        "seattle_data_describe_dataset",
    )
    assert out["row_count"]["origin"] == "live_query" and out["row_count"]["value"] > 2_000_000
    coverage = out["data_coverage"][0]
    assert coverage["column"] == "datetime" and coverage["max"] > coverage["min"]
    assert {"name": "report_location", "type": "point"} in out["columns"]
    assert all(set(c) == {"name", "type"} for c in out["columns"]), "summary columns are name+type"
    assert {"column": "report_location", "type": "point"} in out["geo_columns"]
    kinds = {p["column"]: (p["kind"], p["reliability"]) for p in out["place_fields"]}
    assert kinds[":@computed_region_ru88_fbhk"] == ("neighborhood", "ok")
    assert kinds[":@computed_region_cyqu_gs94"] == ("council_district", "unreliable")
    assert all("values" not in p for p in out["place_fields"]), "values are full-detail only"
    tz = next(t for t in out["time_semantics"] if t["column"] == "datetime")
    assert tz["timezone"] == "America/Los_Angeles" and tz["evidence"]
    assert out["source"]["now_local"]


@pytest.mark.soda_fixture("describe_fire_full")
async def test_describe_full_resolves_region_labels(client, tools_list, soda):
    out = ok(
        await call(
            client,
            "seattle_data_describe_dataset",
            {"dataset_id": "kzjm-xkqj", "detail": "full", "column_query": "type"},
        ),
        tools_list,
        "seattle_data_describe_dataset",
    )
    assert [c["name"] for c in out["columns"]] == ["type"]
    stats = out["columns"][0]["stats"]
    assert stats["top"][0] == "Aid Response" and stats["origin"] == "cached_metadata"
    assert "cardinality" not in stats

    mcpp = next(p for p in out["place_fields"] if p["column"] == ":@computed_region_ru88_fbhk")
    labels = {v["label"]: v["value"] for v in mcpp["values"]}
    assert labels["CAPITOL HILL"] == "8" and labels["NORTH CAPITOL HILL"] == "44"
    assert mcpp["assigned_fraction"] > 0.99
    assert mcpp["label_column"] == "name" and mcpp["boundary_vintage"]

    council = next(p for p in out["place_fields"] if p["column"] == ":@computed_region_cyqu_gs94")
    assert council["assigned_fraction"] < 0.2
    assert council["reliability"] == "unreliable"


@pytest.mark.soda_fixture("describe_crime_full")
async def test_describe_reports_placeholder_share(client, tools_list, soda):
    out = ok(
        await call(
            client,
            "seattle_data_describe_dataset",
            {"dataset_id": "tazs-3rd5", "detail": "full", "column_query": "zzz"},
        ),
        tools_list,
        "seattle_data_describe_dataset",
    )
    assert out["columns"] == []
    hood = next(p for p in out["place_fields"] if p["column"] == "neighborhood")
    assert hood["reliability"] == "partial"
    dash = next(v for v in hood["placeholder_values"] if v["value"] == "-")
    assert dash["rows"] > 500_000
    assert 0.4 < hood["assigned_fraction"] < 0.65
    assert all(v["label"] not in ("-", "UNKNOWN") for v in hood["values"])


@pytest.mark.soda_fixture(
    "describe_fire_summary", overrides={"count%28%2A%29": "timeout", "count(*)": "timeout"}
)
async def test_describe_row_count_timeout_falls_back_to_cache(client, tools_list, soda):
    out = ok(
        await call(client, "seattle_data_describe_dataset", {"dataset_id": "kzjm-xkqj"}),
        tools_list,
        "seattle_data_describe_dataset",
    )
    assert out["row_count"]["status"] == "timeout"
    assert out["row_count"]["origin"] == "cached_metadata"
    assert out["row_count"]["value"] > 0


@pytest.mark.soda_fixture("describe_filter_view")
async def test_describe_filter_view_inherits_parent_facts(client, tools_list, soda):
    out = ok(
        await call(
            client, "seattle_data_describe_dataset", {"dataset_id": "8tqq-u7ib", "sample_rows": 1}
        ),
        tools_list,
        "seattle_data_describe_dataset",
    )
    assert [p["column"] for p in out["place_fields"]] == ["originalzip"]
    assert {"column": "location1", "type": "point"} in out["geo_columns"]
    row = out["sample_rows"][0]
    assert set(row) == {c["name"] for c in out["columns"]}, (
        "sample rows carry every column, nulls included"
    )


@pytest.mark.soda_fixture("describe_not_queryable")
async def test_describe_federated_and_map_views(client, soda):
    federated = (await call(client, "seattle_data_describe_dataset", {"dataset_id": "w3qt-9btr"}))[
        "structuredContent"
    ]
    assert federated["code"] == "dataset_not_queryable"
    assert "arcgis.com" in federated["repair"]["alternative"]

    view = (await call(client, "seattle_data_describe_dataset", {"dataset_id": "5rc4-5s78"}))[
        "structuredContent"
    ]
    assert view["code"] == "dataset_not_queryable"
    assert view["repair"]["arguments"] == {"dataset_id": "76t5-zqzr"}


@pytest.mark.soda_fixture("describe_missing")
async def test_describe_missing_dataset(client, soda):
    result = await call(client, "seattle_data_describe_dataset", {"dataset_id": "abcd-1234"})
    envelope = result["structuredContent"]
    assert result["isError"] is True
    assert envelope["code"] == "dataset_not_found"
    assert envelope["repair"]["tool"] == "seattle_data_search_datasets"


@pytest.mark.soda_fixture("describe_missing", overrides={"abcd-1234": (503, {"message": "down"})})
async def test_describe_upstream_outage_is_temporary(client, soda):
    envelope = (await call(client, "seattle_data_describe_dataset", {"dataset_id": "abcd-1234"}))[
        "structuredContent"
    ]
    assert envelope["code"] == "upstream_unavailable"
    assert envelope["temporary"] is True
