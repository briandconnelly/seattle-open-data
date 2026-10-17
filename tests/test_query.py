"""Milestone 3: the query engine against recorded data.seattle.gov responses.

Re-record with `RECORD_FIXTURES=1 uv run pytest tests/test_query.py`.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import jsonschema
import pytest

from seattle_open_data.constants import RESULT_BYTE_BUDGET
from seattle_open_data.core import query
from tests.conftest import wire

TOOL = "seattle_data_query_dataset"
FIRE = "kzjm-xkqj"
CRIME = "tazs-3rd5"
SEPT = "datetime between '2026-09-01T00:00:00' and '2026-09-30T23:59:59'"
THREE_CALLS = "incident_number in ('F260124133', 'F260124199', 'F260124257')"


async def q(client, tools_list, args):
    result = wire(await client.call_tool_mcp(TOOL, args))
    if not result["isError"]:
        schema = next(t["outputSchema"] for t in tools_list["tools"] if t["name"] == TOOL)
        jsonschema.validate(result["structuredContent"], schema)
    return result


def params_of(request_key: str) -> dict[str, str]:
    return dict(json.loads(request_key.split("?", 1)[1]))


def resource_requests(soda) -> list[dict[str, str]]:
    return [params_of(k) for k in getattr(soda, "requests", []) if "/resource/" in k]


@pytest.mark.soda_fixture("query_aggregate_pages")
async def test_aggregate_pages_by_offset_with_group_tiebreak(client, tools_list, soda):
    args = {
        "dataset_id": FIRE,
        "select": ["type", "count(*) AS n"],
        "group_by": ["type"],
        "order_by": ["n DESC"],
        "page_size": 5,
    }
    first = (await q(client, tools_list, args))["structuredContent"]
    assert (
        first["returned_rows"] == 5 and first["has_more"] and first["consistency"] == "best_effort"
    )
    assert first["rows"][0] == {"type": "Aid Response", "n": first["rows"][0]["n"]}
    second = (await q(client, tools_list, {"dataset_id": FIRE, "cursor": first["next_cursor"]}))[
        "structuredContent"
    ]
    assert {r["type"] for r in second["rows"]}.isdisjoint(r["type"] for r in first["rows"])
    sent = resource_requests(soda)
    if sent:
        assert sent[0]["$order"] == "n DESC, type", "group keys break ties"
        assert sent[1]["$offset"] == "5" and sent[1]["$limit"] == "6"


@pytest.mark.soda_fixture("query_keyset_pages")
async def test_unordered_rows_page_by_hidden_id_keyset(client, tools_list, soda):
    args = {
        "dataset_id": FIRE,
        "select": ["incident_number", "datetime"],
        "where": SEPT,
        "page_size": 3,
    }
    first = (await q(client, tools_list, args))["structuredContent"]
    assert first["consistency"] == "stable"
    assert [c["name"] for c in first["columns"]] == ["incident_number", "datetime"], (
        ":id stays hidden"
    )
    assert all(":id" not in r for r in first["rows"])
    second = (await q(client, tools_list, {"dataset_id": FIRE, "cursor": first["next_cursor"]}))[
        "structuredContent"
    ]
    assert {r["incident_number"] for r in second["rows"]}.isdisjoint(
        r["incident_number"] for r in first["rows"]
    )
    sent = resource_requests(soda)
    if sent:
        assert sent[0]["$order"] == ":id" and "$offset" not in sent[1]
        assert ":id > '" in sent[1]["$where"] and sent[1]["$where"].startswith(f"({SEPT})")


@pytest.mark.soda_fixture("query_ordered")
async def test_ordered_rows_add_id_tiebreak(client, tools_list, soda):
    args = {
        "dataset_id": FIRE,
        "select": ["incident_number", "datetime"],
        "where": SEPT,
        "order_by": ["datetime DESC"],
        "page_size": 2,
    }
    out = (await q(client, tools_list, args))["structuredContent"]
    assert out["rows"][0]["datetime"] >= out["rows"][1]["datetime"]
    sent = resource_requests(soda)
    if sent:
        assert sent[0]["$order"] == "datetime DESC, :id"


@pytest.mark.soda_fixture("query_wide_rows")
async def test_byte_budget_cuts_whole_rows_and_continues_after_them(client, tools_list, soda):
    args = {"dataset_id": "76t5-zqzr", "page_size": 200}
    result = await q(client, tools_list, args)
    out = result["structuredContent"]
    assert 0 < out["returned_rows"] < 200, "the byte budget, not page_size, ended this page"
    assert out["has_more"] is True
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= RESULT_BYTE_BUDGET
    assert all(set(r) == {c["name"] for c in out["columns"]} for r in out["rows"]), "nulls restored"
    nxt = (
        await q(
            client,
            tools_list,
            {"dataset_id": "76t5-zqzr", "cursor": out["next_cursor"], "page_size": 1},
        )
    )["structuredContent"]
    assert nxt["rows"][0]["permitnum"] not in {r["permitnum"] for r in out["rows"]}


@pytest.mark.soda_fixture("query_three_rows")
async def test_cap_reached_exactly_is_not_truncated(client, tools_list, soda, monkeypatch):
    monkeypatch.setattr(query, "QUERY_LOGICAL_CAP", 3)
    out = (
        await q(
            client,
            tools_list,
            {
                "dataset_id": FIRE,
                "select": ["incident_number"],
                "where": THREE_CALLS,
                "page_size": 5,
            },
        )
    )["structuredContent"]
    assert out["returned_rows"] == 3
    assert out["truncated"] is False and out["has_more"] is False


@pytest.mark.soda_fixture("query_three_rows_capped")
async def test_cap_exceeded_is_truncated_without_cursor(client, tools_list, soda, monkeypatch):
    monkeypatch.setattr(query, "QUERY_LOGICAL_CAP", 2)
    first = (
        await q(
            client,
            tools_list,
            {
                "dataset_id": FIRE,
                "select": ["incident_number"],
                "where": THREE_CALLS,
                "page_size": 1,
            },
        )
    )["structuredContent"]
    assert first["has_more"] is True and first["truncated"] is False
    last = (await q(client, tools_list, {"dataset_id": FIRE, "cursor": first["next_cursor"]}))[
        "structuredContent"
    ]
    assert last["returned_rows"] == 1
    assert last["truncated"] is True and last["has_more"] is False and "next_cursor" not in last
    assert last["truncation_reason"] == "row_cap"


@pytest.mark.soda_fixture("query_raw_soql")
async def test_raw_soql_is_one_page(client, tools_list, soda):
    soql = "SELECT type, count(*) AS n GROUP BY type ORDER BY n DESC LIMIT 50"
    out = (
        await q(
            client, tools_list, {"dataset_id": FIRE, "mode": "soql", "soql": soql, "page_size": 5}
        )
    )["structuredContent"]
    assert out["returned_rows"] == 5 and out["consistency"] == "single_page"
    assert out["truncated"] is True and out["truncation_reason"] == "raw_soql_not_paged"
    assert "LIMIT 5" in out["truncation_hint"]
    sent = resource_requests(soda)
    if sent:
        assert sent[-1] == {"$query": soql}, "raw SoQL is sent verbatim with nothing added"

    fits = "SELECT type, count(*) AS n GROUP BY type ORDER BY n DESC LIMIT 3"
    small = (
        await q(
            client, tools_list, {"dataset_id": FIRE, "mode": "soql", "soql": fits, "page_size": 5}
        )
    )["structuredContent"]
    assert small["returned_rows"] == 3 and small["truncated"] is False


@pytest.mark.soda_fixture("query_coverage_warning")
async def test_unreliable_place_field_warns(client, tools_list, soda):
    out = (
        await q(
            client,
            tools_list,
            {
                "dataset_id": FIRE,
                "select": ["count(*) AS n"],
                "where": ":@computed_region_cyqu_gs94 = '3'",
            },
        )
    )["structuredContent"]
    warning = out["coverage_warnings"][0]
    assert warning["column"] == ":@computed_region_cyqu_gs94"
    assert warning["reliability"] == "unreliable"


@pytest.mark.soda_fixture("query_coverage_grouped")
async def test_grouping_by_a_partial_place_field_warns_in_both_modes(client, tools_list, soda):
    clauses = {
        "dataset_id": CRIME,
        "select": ["neighborhood", "count(*) AS n"],
        "group_by": ["neighborhood"],
        "order_by": ["n DESC"],
        "page_size": 3,
    }
    soql = "SELECT neighborhood, count(*) AS n GROUP BY neighborhood ORDER BY n DESC LIMIT 3"
    raw = {"dataset_id": CRIME, "mode": "soql", "soql": soql, "page_size": 3}
    for args in (clauses, raw):
        out = (await q(client, tools_list, args))["structuredContent"]
        assert [(w["column"], w["reliability"]) for w in out.get("coverage_warnings") or []] == [
            ("neighborhood", "partial")
        ], args.get("mode", "clauses")


@pytest.mark.soda_fixture("query_coverage_implied_select")
async def test_omitted_select_does_not_warn_for_every_place_column(client, tools_list, soda):
    out = (
        await q(
            client,
            tools_list,
            {"dataset_id": CRIME, "where": "offense_id = '0'", "page_size": 1},
        )
    )["structuredContent"]
    assert out.get("coverage_warnings") is None
    sent = resource_requests(soda)
    if sent:
        assert "neighborhood" in sent[-1]["$select"], "the implied select does include the field"


@pytest.mark.soda_fixture("query_near")
async def test_near_on_location_column_with_urls(client, tools_list, soda):
    near = {"column": "location1", "latitude": 47.6062, "longitude": -122.3321, "radius_m": 500}
    out = (
        await q(
            client,
            tools_list,
            {
                "dataset_id": "76t5-zqzr",
                "select": ["permitnum"],
                "near": near,
                "page_size": 2,
                "detail": "full",
            },
        )
    )["structuredContent"]
    assert out["returned_rows"] == 2
    assert "within_circle(location1, 47.6062, -122.3321, 500.0)" in out["effective_query"]
    download = parse_qs(urlsplit(out["download_url"]).query)
    assert out["download_url"].startswith("https://data.seattle.gov/resource/76t5-zqzr.csv?")
    assert download["$limit"] == ["1000000000"] and "$offset" not in download
    assert "within_circle" in download["$where"][0]


@pytest.mark.soda_fixture("query_spd_view")
async def test_near_on_redacted_text_column_repairs_to_inspection(client, tools_list, soda):
    near = {"column": "dispatch_latitude", "latitude": 47.6, "longitude": -122.3, "radius_m": 500}
    envelope = (await q(client, tools_list, {"dataset_id": "33kz-ixgy", "near": near}))[
        "structuredContent"
    ]
    assert envelope["code"] == "column_not_geographic"
    assert envelope["repair"]["arguments"] == {"dataset_id": "33kz-ixgy", "detail": "full"}
    assert "dispatch_neighborhood" in envelope["repair"]["alternative"]
    assert "not equivalent" in envelope["repair"]["alternative"]


@pytest.mark.soda_fixture("query_bad_column")
async def test_unknown_column_repairs_to_describe(client, tools_list, soda):
    envelope = (await q(client, tools_list, {"dataset_id": FIRE, "select": ["nope"]}))[
        "structuredContent"
    ]
    assert envelope["code"] == "no_such_column"
    assert envelope["repair"]["tool"] == "seattle_data_describe_dataset"
    assert "position" not in envelope["details"], "positions of the assembled query are hidden"
    assert FIRE not in query._views, "a stale schema is dropped"


@pytest.mark.soda_fixture("query_three_rows", overrides={"/resource/": "timeout"})
async def test_timeout_repairs_toward_narrowing(client, tools_list, soda):
    envelope = (
        await q(
            client,
            tools_list,
            {"dataset_id": FIRE, "select": ["incident_number"], "where": THREE_CALLS},
        )
    )["structuredContent"]
    assert envelope["code"] == "query_timeout" and envelope["temporary"] is True
    assert envelope["repair"]["next_step"] == "narrow"


@pytest.mark.soda_fixture("query_three_rows")
async def test_cursor_for_another_dataset_is_rejected(client, tools_list, soda):
    first = (
        await q(
            client,
            tools_list,
            {
                "dataset_id": FIRE,
                "select": ["incident_number"],
                "where": THREE_CALLS,
                "page_size": 1,
            },
        )
    )["structuredContent"]
    envelope = (
        await q(client, tools_list, {"dataset_id": "76t5-zqzr", "cursor": first["next_cursor"]})
    )["structuredContent"]
    assert envelope["code"] == "invalid_cursor"
    assert envelope["repair"]["arguments"] == {"dataset_id": "76t5-zqzr"}


def _json_size(clauses: dict) -> int:
    return len(json.dumps(clauses, separators=(",", ":"), ensure_ascii=False).encode())


def _at_cap(key: str, make) -> dict:
    """The largest clauses `make(n)` builds whose compact JSON fits the 4 KiB cap."""
    n = 0
    while _json_size({key: make(n + 1)}) <= 4096:
        n += 1
    return {key: make(n)}


def _where(char: str):
    return lambda n: f"incident_number = '{char * n}'"


_TWO_ROWS = [{":id": "row-1", "incident_number": "a"}, {":id": "row-2", "incident_number": "b"}]
_AT_CAP = {
    "ascii": _at_cap("where", _where("x")),
    "quotes": _at_cap("where", _where('"')),
    "non-ascii": _at_cap("where", _where("é")),
    "many-list-items": _at_cap("order_by", lambda n: ["incident_number"] * n),
}


@pytest.mark.parametrize("clauses", list(_AT_CAP.values()), ids=list(_AT_CAP))
@pytest.mark.soda_fixture("query_three_rows", overrides={"/resource/": (200, _TWO_ROWS)})
async def test_clauses_at_the_cap_page_with_a_cursor_within_6_kib(
    client, tools_list, soda, clauses
):
    result = (await q(client, tools_list, {"dataset_id": FIRE, "page_size": 1, **clauses}))[
        "structuredContent"
    ]
    assert len(result["next_cursor"]) <= 6 * 1024


@pytest.mark.parametrize(
    "clauses",
    [
        pytest.param({"where": "incident_number = '" + '"' * 4070 + "'"}, id="quotes"),
        pytest.param({"order_by": ["incident_number"] * 240}, id="many-list-items"),
    ],
)
async def test_clauses_over_the_cap_as_json_are_rejected(client, tools_list, clauses):
    assert _json_size(clauses) > 4096
    envelope = (await q(client, tools_list, {"dataset_id": FIRE, **clauses}))["structuredContent"]
    assert envelope["code"] == "invalid_argument"
