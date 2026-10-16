from __future__ import annotations

from seattle_open_data.mcp.schema import compact_schema


def test_collapses_optional_union_and_drops_titles():
    schema = {
        "title": "Args",
        "type": "object",
        "properties": {
            "title": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Title",
                "description": "d",
            },
        },
    }
    assert compact_schema(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string", "description": "d"}},
    }


def test_keeps_real_unions():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert compact_schema(schema) == schema


def test_keeps_non_null_defaults():
    assert compact_schema({"type": "integer", "default": 0}) == {"type": "integer", "default": 0}
