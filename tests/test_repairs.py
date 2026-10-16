"""Milestone 1 exit test (e): every documented or emitted repair is a literal, valid call."""

from __future__ import annotations

import jsonschema
import pytest

from seattle_open_data.core.errors import ErrorCode, ErrorEnvelope, map_upstream_error
from seattle_open_data.core.reference import ERROR_CODES_BY_TOOL, ERROR_EXAMPLES
from tests.conftest import wire


def assert_repair_callable(repair: dict, input_schemas: dict) -> None:
    assert repair["tool"] in input_schemas, f"repair names unknown tool {repair['tool']}"
    jsonschema.validate(repair["arguments"], input_schemas[repair["tool"]])
    for value in repair["arguments"].values():
        assert "<" not in str(value), "repair arguments must be literal, not placeholders"


def test_every_documented_code_has_a_worked_example():
    documented = {code for codes in ERROR_CODES_BY_TOOL.values() for code in codes}
    assert documented <= ERROR_EXAMPLES.keys()
    assert set(ErrorCode) == documented, "every code is reachable from some tool"


@pytest.mark.parametrize("code", list(ERROR_EXAMPLES))
def test_worked_example_is_a_valid_envelope_with_callable_repair(code, input_schemas):
    example = ERROR_EXAMPLES[code]
    ErrorEnvelope.model_validate(example)
    assert example["code"] == code.value
    assert "retry_after_ms" in example
    if example["temporary"] is False:
        assert example["retry_after_ms"] is None
    if "repair" in example:
        assert_repair_callable(example["repair"], input_schemas)


UPSTREAM_CASES = [
    (400, {"code": "query.soql.no-such-column"}),
    (400, {"code": "query.soql.type-mismatch"}),
    (403, {"message": "Invalid app_token specified"}),
    (404, {}),
]


@pytest.mark.parametrize(("status", "body"), UPSTREAM_CASES)
def test_upstream_mapping_repairs_are_callable(status, body, input_schemas):
    repair = map_upstream_error(
        status, body, dataset_id="kzjm-xkqj", token_configured=True
    ).envelope.repair
    assert repair is not None
    assert_repair_callable(repair.model_dump(exclude_none=True), input_schemas)


EMITTED_CASES = [
    ("seattle_data_get_reference", {"topic": "soql", "item_id": "joins"}),
    ("seattle_data_get_reference", {"item_id": "dates"}),
]


@pytest.mark.parametrize(("tool", "args"), EMITTED_CASES)
async def test_emitted_repairs_are_callable(client, input_schemas, tool, args):
    envelope = wire(await client.call_tool_mcp(tool, args))["structuredContent"]
    assert "repair" in envelope
    assert_repair_callable(envelope["repair"], input_schemas)
    followed = wire(
        await client.call_tool_mcp(envelope["repair"]["tool"], envelope["repair"]["arguments"])
    )
    assert followed["structuredContent"].get("code") != "invalid_argument", (
        "following a repair must not be a usage error"
    )
