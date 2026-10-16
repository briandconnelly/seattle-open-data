"""SODA2 and Discovery API access. Every upstream failure becomes a `SeattleDataError`."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from seattle_open_data import __version__
from seattle_open_data.constants import PUBLIC_HOST, UPSTREAM_MAX_BYTES, UPSTREAM_TIMEOUT_SECONDS
from seattle_open_data.core.errors import (
    ErrorCode,
    ErrorDetails,
    SeattleDataError,
    map_upstream_error,
)

TOKEN_ENV = "SOCRATA_APP_TOKEN"


class SodaClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        token = os.environ.get(TOKEN_ENV) or None
        headers = {"User-Agent": f"seattle-open-data/{__version__}", "Accept": "application/json"}
        if token:
            headers["X-App-Token"] = token
        self.token_configured = token is not None
        self._http = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS, connect=10.0),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        dataset_id: str | None = None,
        timeout: float = UPSTREAM_TIMEOUT_SECONDS,
    ) -> Any:
        try:
            response = await self._http.get(url, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise SeattleDataError(
                ErrorCode.QUERY_TIMEOUT,
                f"The upstream service did not answer within {timeout:.0f} s.",
            ) from exc
        except httpx.TransportError as exc:
            raise SeattleDataError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The upstream service could not be reached.",
                details=ErrorDetails(reason=type(exc).__name__),
            ) from exc
        if len(response.content) > UPSTREAM_MAX_BYTES:
            raise SeattleDataError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The upstream response exceeded the size limit.",
                details=ErrorDetails(reason=f"More than {UPSTREAM_MAX_BYTES} bytes."),
                temporary=False,
            )
        body = _json_or_none(response)
        if response.status_code >= 400:
            raise map_upstream_error(
                response.status_code,
                body if isinstance(body, dict) else None,
                dict(response.headers),
                dataset_id=dataset_id,
                token_configured=self.token_configured,
            )
        if body is None:
            raise SeattleDataError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The upstream service returned a non-JSON response.",
                details=ErrorDetails(reason=f"HTTP {response.status_code}."),
            )
        return body

    async def view(self, dataset_id: str) -> dict[str, Any]:
        return await self.get_json(
            f"https://{PUBLIC_HOST}/api/views/{dataset_id}.json", dataset_id=dataset_id
        )

    async def soql(
        self, dataset_id: str, params: dict[str, Any], *, timeout: float = UPSTREAM_TIMEOUT_SECONDS
    ) -> list[dict[str, Any]]:
        rows = await self.get_json(
            f"https://{PUBLIC_HOST}/resource/{dataset_id}.json",
            params,
            dataset_id=dataset_id,
            timeout=timeout,
        )
        return rows if isinstance(rows, list) else []


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


_client: SodaClient | None = None


def get_client() -> SodaClient:
    global _client  # noqa: PLW0603 - one pooled client per process.
    if _client is None:
        _client = SodaClient()
    return _client


def set_client(client: SodaClient | None) -> None:
    global _client  # noqa: PLW0603
    _client = client
