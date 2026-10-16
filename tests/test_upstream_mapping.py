"""Upstream SODA2 failures map onto the error taxonomy (docs/design.md: Errors).

Covers every query.soql.* variant.
"""

from __future__ import annotations

from typing import Any

from seattle_open_data.core.errors import map_upstream_error


def mapped(status: int, body: Any = None, headers: Any = None, **kwargs: Any) -> dict[str, Any]:
    return map_upstream_error(status, body, headers, **kwargs).envelope.to_wire()


def test_no_such_column_with_position():
    body = {
        "code": "query.soql.no-such-column",
        "error": True,
        "message": "No such column: foo",
        "data": {"column": "foo", "position": {"line": 1, "column": 8, "row": 1}},
    }
    err = mapped(400, body, dataset_id="kzjm-xkqj")
    assert err["code"] == "no_such_column"
    assert err["details"]["position"] == {"line": 1, "column": 8, "row": 1}
    assert err["repair"] == {
        "next_step": "inspect_columns",
        "tool": "seattle_data_describe_dataset",
        "arguments": {"dataset_id": "kzjm-xkqj", "detail": "full"},
    }
    assert "foo" not in err["message"], "upstream text is not echoed"


def test_code_only_in_message_text():
    body = {"message": "Query coordinator error: query.soql.no-such-column; No such column: x"}
    assert mapped(400, body, dataset_id="kzjm-xkqj")["code"] == "no_such_column"


def test_type_mismatch_is_syntax_error_with_position():
    body = {
        "errorCode": "query.soql.type-mismatch",
        "message": "Type mismatch",
        "data": {"position": {"line": 1, "column": 22}},
    }
    err = mapped(400, body)
    assert err["code"] == "soql_syntax_error"
    assert err["details"]["upstream_code"] == "query.soql.type-mismatch"
    assert err["details"]["position"] == {"line": 1, "column": 22}


def test_invalid_without_position():
    err = mapped(400, {"code": "query.soql.invalid", "data": {}})
    assert err["code"] == "soql_syntax_error"
    assert "position" not in err["details"]
    assert err["repair"]["arguments"] == {"topic": "soql", "item_id": "syntax"}


def test_rejected_token_only_when_configured():
    body = {"code": "permission_denied", "message": "Invalid app_token specified"}
    assert mapped(403, body, token_configured=True)["code"] == "app_token_rejected"
    assert mapped(403, body, token_configured=False)["code"] == "upstream_forbidden"


def test_generic_forbidden():
    err = mapped(403, {"code": "permission_denied", "message": "Forbidden"}, token_configured=True)
    assert err["code"] == "upstream_forbidden"
    assert "repair" not in err


def test_rate_limited_reads_retry_after():
    err = mapped(429, {}, {"Retry-After": "12"})
    assert err["code"] == "rate_limited"
    assert err["temporary"] is True
    assert err["retry_after_ms"] == 12_000


def test_rate_limited_has_no_repair():
    """temporary=true already means retry unchanged; a retry repair would echo caller text."""
    assert "repair" not in mapped(429, {}, {"Retry-After": "3"})


def test_rate_limited_without_header():
    err = mapped(429)
    assert err["temporary"] is True
    assert err["retry_after_ms"] is None


def test_server_error_is_temporary():
    err = mapped(503)
    assert err["code"] == "upstream_unavailable"
    assert err["temporary"] is True


def test_not_found_points_to_search():
    err = mapped(404, {"code": "not_found"}, dataset_id="abcd-1234")
    assert err["code"] == "dataset_not_found"
    assert err["details"]["field"] == "dataset_id"
    assert err["repair"]["tool"] == "seattle_data_search_datasets"
    assert err["repair"]["arguments"] == {}


def test_non_temporary_never_carries_delay():
    err = mapped(403, {}, {"Retry-After": "5"})
    assert err["temporary"] is False
    assert err["retry_after_ms"] is None
