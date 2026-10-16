"""Publisher text is untrusted data [3.sec-untrusted-content] [3.sec-sanitize]."""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|br|div|li|ul|ol|h[1-6]|tr)\b[^>]*>", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def clean(text: str | None, *, limit: int | None = None) -> str | None:
    """Strip control characters; collapse runs of spaces."""
    if text is None:
        return None
    out = _SPACE.sub(" ", _CONTROL.sub("", str(text))).strip()
    if limit is not None and len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


def html_to_text(markup: str | None, *, limit: int | None = None) -> str | None:
    if not markup:
        return None
    text = _BLOCK.sub("\n", markup)
    text = html.unescape(_TAG.sub("", text))
    text = _BLANK_LINES.sub("\n", text)
    return clean(text, limit=limit) or None
