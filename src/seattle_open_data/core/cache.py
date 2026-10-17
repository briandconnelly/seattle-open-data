"""Per-process caches of upstream metadata (docs/design.md: Implicit state).

Only metadata is cached here: dataset schemas and region-layer labels. Rows,
counts, and coverage are always live.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from seattle_open_data.constants import (
    REGION_LABEL_CACHE_MAX,
    REGION_LABEL_TTL_SECONDS,
    VIEW_CACHE_MAX,
    VIEW_TTL_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Hashable

    from seattle_open_data.core.http import SodaClient


class TtlCache[K: Hashable, V]:
    """Entries expire after `ttl` seconds; past `max_entries`, the oldest write is evicted."""

    def __init__(self, ttl: float, max_entries: int) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        hit = self._entries.get(key)
        if hit is None:
            return None
        if time.monotonic() - hit[0] >= self.ttl:
            del self._entries[key]
            return None
        return hit[1]

    def put(self, key: K, value: V) -> None:
        # Re-inserting moves the key to the end, so eviction order follows write time.
        self._entries.pop(key, None)
        self._entries[key] = (time.monotonic(), value)
        while len(self._entries) > self.max_entries:
            del self._entries[next(iter(self._entries))]

    def pop(self, key: K) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __contains__(self, key: object) -> bool:
        return self.get(key) is not None  # ty: ignore[invalid-argument-type]

    def __len__(self) -> int:
        return len(self._entries)


views: TtlCache[str, dict[str, Any]] = TtlCache(VIEW_TTL_SECONDS, VIEW_CACHE_MAX)
#: `(layer_id, feature_id)` to label. Boundary layers change on redistricting, not daily.
region_labels: TtlCache[tuple[str, str], str] = TtlCache(
    REGION_LABEL_TTL_SECONDS, REGION_LABEL_CACHE_MAX
)


async def cached_view(client: SodaClient, dataset_id: str) -> dict[str, Any]:
    view = views.get(dataset_id)
    return view if view is not None else await fresh_view(client, dataset_id)


async def fresh_view(client: SodaClient, dataset_id: str) -> dict[str, Any]:
    """Fetch the schema and store it, so the query that usually follows reuses it."""
    view = await client.view(dataset_id)
    views.put(dataset_id, view)
    return view


def clear() -> None:
    views.clear()
    region_labels.clear()
