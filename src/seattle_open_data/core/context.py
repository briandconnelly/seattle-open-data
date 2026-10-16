"""Per-call provenance: request id and Seattle wall-clock time."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from seattle_open_data.constants import SEATTLE_TZ
from seattle_open_data.core.models import Source

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    request_id = uuid.uuid4().hex[:16]
    _request_id.set(request_id)
    return request_id


def current_request_id() -> str:
    return _request_id.get() or new_request_id()


def make_source(*, retrieved: bool = False) -> Source:
    now = datetime.now(UTC)
    return Source(
        request_id=current_request_id(),
        now_local=now.astimezone(SEATTLE_TZ).isoformat(timespec="seconds"),
        retrieved_at=now.isoformat(timespec="seconds").replace("+00:00", "Z")
        if retrieved
        else None,
    )
