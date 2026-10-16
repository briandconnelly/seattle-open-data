"""Shrink generated JSON Schemas to the portable subset agents receive.

See [2.compact-baseline] and [3.portable-schema].
"""

from __future__ import annotations

from typing import Any

_DROP = frozenset({"title"})


def compact_schema(schema: Any) -> Any:
    """Drop titles and `default: null`, and collapse `anyOf: [X, null]` to X.

    Optional parameters are declared `T | None = None` in Python so omission and
    an explicit null mean the same thing; agents should only ever see `T`.
    """
    if isinstance(schema, list):
        return [compact_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _DROP and isinstance(value, str):
            continue
        if key == "default" and value is None:
            continue
        if key in ("properties", "$defs", "patternProperties"):
            # Keys here are property names, not schema keywords: a property may be named "title".
            out[key] = {name: compact_schema(sub) for name, sub in value.items()}
        else:
            out[key] = compact_schema(value)

    any_of = out.get("anyOf")
    if isinstance(any_of, list):
        non_null = [s for s in any_of if s != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) < len(any_of):
            merged = {k: v for k, v in out.items() if k != "anyOf"}
            return {**non_null[0], **merged}
    return out
