"""Agents learn where and how to report a defect in this server, in-band.

The tracker was named only in pyproject.toml, manifest.json, and the README, none of which an
agent reads mid-failure.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from seattle_open_data.constants import ISSUES_URL
from seattle_open_data.core.errors import ErrorCode
from seattle_open_data.core.reference import CAPABILITIES, INSTRUCTIONS
from tests.test_review_separation import _instruction_rules, _section

ROOT = Path(__file__).resolve().parent.parent


def test_issues_url_matches_pyproject_and_manifest():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert pyproject["project"]["urls"]["Issues"] == ISSUES_URL
    assert manifest["support"] == ISSUES_URL


async def test_instructions_reach_the_client_with_the_tracker(client):
    assert ISSUES_URL in client.instructions
    rule = next(rule for rule in _instruction_rules() if ISSUES_URL in rule)
    assert "never file unasked" in rule
    assert "request_id" in rule


async def test_capabilities_resource_says_what_is_and_is_not_a_defect(client):
    text = (await client.read_resource("seattle-data://capabilities"))[0].text
    assert text == CAPABILITIES
    defects = _section(CAPABILITIES, "Defects")
    assert ISSUES_URL in defects
    is_defect, not_defect = (
        next(line for line in defects.splitlines() if line.startswith(prefix))
        for prefix in ("- A defect", "- Not a defect")
    )
    assert "`internal_error`" in is_defect
    for code in ("upstream_unavailable", "rate_limited", "app_token_rejected", "invalid_argument"):
        assert f"`{code}`" in not_defect


def test_defects_section_names_only_real_codes():
    named = set(re.findall(r"`([a-z_]+)`", _section(CAPABILITIES, "Defects")))
    codes = {code.value for code in ErrorCode}
    assert named - {"request_id", "repair"} <= codes


def test_instructions_rule_points_at_a_real_section():
    assert "## Defects" in CAPABILITIES
    assert "capabilities ## Defects" in INSTRUCTIONS


def test_guide_query_clause_defers_to_the_non_defect_codes():
    """A guide query can hit rate_limited or upstream_unavailable; that is not a server defect."""
    is_defect = next(
        line
        for line in _section(CAPABILITIES, "Defects").splitlines()
        if line.startswith("- A defect")
    )
    guide_clause = is_defect[is_defect.index("a guide's verified query") :]
    assert "not a defect" in guide_clause
