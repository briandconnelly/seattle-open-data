"""Stateless, HMAC-signed cursors (PLAN §5). The key lives in process memory only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from seattle_open_data.constants import CURSOR_TTL_SECONDS
from seattle_open_data.core.errors import ErrorCode, ErrorDetails, Repair, SeattleDataError

_KEY = secrets.token_bytes(32)
_VERSION = 1


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode(kind: str, state: dict[str, Any]) -> str:
    payload = json.dumps(
        {"v": _VERSION, "k": kind, "t": int(time.time()), "s": state},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(_KEY, payload, hashlib.sha256).digest()[:16]
    return f"{_b64(payload)}.{_b64(signature)}"


def decode(cursor: str, kind: str, *, restart: Repair) -> dict[str, Any]:
    """Verify and unpack.

    A bad signature is `cursor_expired`: a server restart rotates the in-memory key.
    """
    try:
        payload_part, signature_part = cursor.split(".", 1)
        payload = _unb64(payload_part)
        signature = _unb64(signature_part)
        data = json.loads(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        raise _error(
            ErrorCode.INVALID_CURSOR,
            "The cursor is malformed.",
            "It could not be decoded.",
            restart,
        ) from exc
    expected = hmac.new(_KEY, payload, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(signature, expected):
        raise _error(
            ErrorCode.CURSOR_EXPIRED,
            "The cursor has expired or the server restarted.",
            "Its signature does not match: cursors do not survive a server restart.",
            restart,
        )
    if data.get("v") != _VERSION:
        raise _error(
            ErrorCode.INVALID_CURSOR,
            "The cursor uses an unsupported format.",
            "It was issued by a different server version.",
            restart,
        )
    if data.get("k") != kind:
        raise _error(
            ErrorCode.INVALID_CURSOR,
            "The cursor belongs to a different tool.",
            "Use a cursor only with the tool that returned it.",
            restart,
        )
    if time.time() - int(data.get("t", 0)) > CURSOR_TTL_SECONDS:
        raise _error(
            ErrorCode.CURSOR_EXPIRED, "The cursor has expired.", "Cursors last 1 hour.", restart
        )
    return data["s"]


def _error(code: ErrorCode, message: str, reason: str, restart: Repair) -> SeattleDataError:
    return SeattleDataError(
        code, message, details=ErrorDetails(field="cursor", reason=reason), repair=restart
    )
