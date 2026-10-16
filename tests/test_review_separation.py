"""Regressions for the context-vs-constraints review of the prose surfaces."""

from __future__ import annotations

import re

import pytest

from seattle_open_data.constants import INSTRUCTIONS_SENTINEL
from seattle_open_data.core.guides import GUIDES
from seattle_open_data.core.reference import CAPABILITIES, INSTRUCTIONS


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"^## {heading}\n(.*?)(?=^## |\Z)", markdown, re.MULTILINE | re.DOTALL)
    assert match, f"no ## {heading} section"
    return match.group(1)


def _instruction_rules() -> list[str]:
    lines: list[str] = list(INSTRUCTIONS.splitlines())
    rules = lines[lines.index("Rules:") + 1 : lines.index(INSTRUCTIONS_SENTINEL)]
    assert rules
    assert all(line.startswith("- ") for line in rules)
    return rules


# --- F1: the full contract restates every rule the instructions carry -----------


def test_capabilities_rules_restate_every_instructions_rule():
    rules = _section(CAPABILITIES, "Rules").splitlines()
    missing = [rule for rule in _instruction_rules() if rule not in rules]
    assert not missing, f"instructions rules absent from CAPABILITIES ## Rules: {missing}"


def test_capabilities_keeps_facts_out_of_rules():
    assert "## Semantics" in CAPABILITIES
    rules = _section(CAPABILITIES, "Rules")
    for fact in ("have no timezone", "can lag `updated_at`", "can shift if the dataset"):
        assert fact not in rules


# --- F2: untrusted dataset text is a rule in the head ---------------------------


def test_untrusted_dataset_text_is_a_head_rule():
    assert any("publisher data" in rule for rule in _instruction_rules())


# --- F3: the area and coverage rules are complete and checkable -----------------


def test_area_rule_covers_fields_without_listed_values():
    area = next(rule for rule in _instruction_rules() if '"in <area>"' in rule)
    assert "group_by" in area


def test_coverage_rule_names_reliability_levels():
    coverage = next(rule for rule in _instruction_rules() if "coverage_warnings" in rule)
    assert "near-zero" not in coverage
    assert "unreliable" in coverage
    assert "partial" in coverage


# --- F4, F5: no dead parameters; every top-level parameter is described ---------


async def test_reference_tool_has_no_dead_parameters(input_schemas, tools_list):
    assert set(input_schemas["seattle_data_get_reference"]["properties"]) == {"topic", "item_id"}
    reference = next(t for t in tools_list["tools"] if t["name"] == "seattle_data_get_reference")
    assert not {"chunk_id", "next_chunk_id"} & set(reference["outputSchema"]["properties"])


async def test_every_parameter_has_a_description(input_schemas):
    undocumented = [
        f"{tool}.{name}"
        for tool, schema in input_schemas.items()
        for name, prop in schema["properties"].items()
        if not prop.get("description")
    ]
    assert not undocumented


# --- F7: guide rules sit in their own section, ahead of the facts ---------------


@pytest.mark.parametrize("guide", GUIDES, ids=[g.guide_id for g in GUIDES])
def test_guide_rules_come_first(guide):
    rendered = guide.render()
    headings = re.findall(r"^## (.+)$", rendered, re.MULTILINE)
    if guide.rules:
        assert headings[0] == "Rules"
    else:
        assert "Rules" not in headings


@pytest.mark.parametrize("guide", GUIDES, ids=[g.guide_id for g in GUIDES])
def test_guide_body_holds_no_directives(guide):
    directives = re.findall(r"\b(always|never|do not|don't|say so)\b", guide.body, re.IGNORECASE)
    assert not directives, f"move these into {guide.guide_id}'s rules: {directives}"


# --- F8: stability uses the closed tier set -------------------------------------


def test_capabilities_states_a_closed_set_stability_tier():
    assert "pre-release" not in CAPABILITIES
    assert re.search(r"^- Stability: (stable|preview|experimental) ", CAPABILITIES, re.MULTILINE)


# --- PR #6 review: rules keep the scope their facts had -------------------------


def test_truncation_rule_is_scoped_to_row_cap():
    # Raw SoQL truncation (`raw_soql_not_paged`) is recovered by advancing OFFSET.
    rules = _section(CAPABILITIES, "Rules")
    assert "When `truncated` is true" not in rules
    assert '`truncation_reason` is `"row_cap"`' in rules


def test_crime_date_rule_allows_the_reporting_column():
    crime = next(g for g in GUIDES if g.guide_id == "spd-crime")
    date_rule = next(rule for rule in crime.rules if "offense_date" in rule)
    assert "report_date_time" in date_rule
    filtered = {ex.arguments["where"].split()[0] for ex in crime.examples}
    assert all(column in date_rule for column in filtered)


def test_fire_calls_caveat_is_scoped_to_all_types():
    fire = next(g for g in GUIDES if g.guide_id == "fire-911")
    caveat = next(rule for rule in fire.rules if "fire calls" in rule)
    assert "all response types" in caveat
