"""SODA2 and Discovery API access. Every upstream failure becomes a `SeattleDataError`."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
import httpx
import ijson

from seattle_open_data import __version__
from seattle_open_data.constants import PUBLIC_HOST, UPSTREAM_MAX_BYTES, UPSTREAM_TIMEOUT_SECONDS
from seattle_open_data.core.errors import (
    ErrorCode,
    ErrorDetails,
    SeattleDataError,
    map_upstream_error,
)

TOKEN_ENV = "SOCRATA_APP_TOKEN"
#: An MCPB host can pass a blank optional setting through unexpanded, e.g. Claude Desktop
#: sends "${user_config.app_token}"; Socrata's firewall answers that header with a 403.
_UNEXPANDED = re.compile(r"\$\{[^}]*\}")

logger = logging.getLogger("seattle_open_data")


def configured_token() -> str | None:
    """SOCRATA_APP_TOKEN, or None when it is unset, blank, or an unexpanded placeholder."""
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if _UNEXPANDED.fullmatch(token):
        logger.warning("%s is an unexpanded placeholder; querying anonymously", TOKEN_ENV)
        return None
    return token or None


class SodaClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        token = configured_token()
        headers = {"User-Agent": f"seattle-open-data/{__version__}", "Accept": "application/json"}
        if token:
            headers["X-App-Token"] = token
        self.token_configured = token is not None
        self._http = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS, connect=10.0),
            # A redirect would carry X-App-Token to wherever it points; none is expected.
            follow_redirects=False,
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
            # httpx timeouts are per read; the overall deadline bounds a trickling response too.
            with anyio.fail_after(timeout):
                async with self._http.stream(
                    "GET", url, params=params, timeout=timeout
                ) as response:
                    content = await _read_capped(response)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise _timeout(timeout) from exc
        except httpx.TransportError as exc:
            raise _unreachable(exc) from exc
        _reject_redirect(response)
        body = _parse_json(content)
        if response.status_code >= 400:
            raise map_upstream_error(
                response.status_code,
                body if isinstance(body, dict) else None,
                dict(response.headers),
                dataset_id=dataset_id,
                token_configured=self.token_configured,
                endpoint=_endpoint(url),
            )
        if body is None:
            raise shape_error(f"HTTP {response.status_code} with a non-JSON body.")
        return body

    async def stream_rows(
        self,
        dataset_id: str,
        params: dict[str, Any],
        *,
        max_rows: int,
        timeout: float = UPSTREAM_TIMEOUT_SECONDS,
    ) -> RowStream:
        """Read at most `max_rows` rows, then close the connection.

        `more` says whether rows remained.

        Raw SoQL cannot carry `$limit` (SODA2 answers 400), so this is what bounds it.
        """
        url = f"https://{PUBLIC_HOST}/resource/{dataset_id}.json"
        out = RowStream()
        try:
            with anyio.fail_after(timeout):
                async with self._http.stream(
                    "GET", url, params=params, timeout=timeout
                ) as response:
                    _reject_redirect(response)
                    if response.status_code >= 400:
                        body = _parse_json(await _read_capped(response))
                        raise map_upstream_error(
                            response.status_code,
                            body if isinstance(body, dict) else None,
                            dict(response.headers),
                            dataset_id=dataset_id,
                            token_configured=self.token_configured,
                        )
                    out.fields = _header_list(response.headers, "X-SODA2-Fields")
                    out.types = _header_list(response.headers, "X-SODA2-Types")
                    reader = _AsyncReader(response)
                    # ijson's "item" prefix silently yields nothing for a non-array root, which
                    # would read as "no rows"; an error object or null must not look like that.
                    if await reader.first_byte() != b"[":
                        raise shape_error("Expected a JSON array of rows.")
                    async for row in ijson.items_async(reader, "item", use_float=False):
                        if not isinstance(row, dict):
                            raise shape_error("Expected each row to be a JSON object.")
                        if len(out.rows) >= max_rows:
                            out.more = True
                            break
                        out.rows.append(row)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise _timeout(timeout) from exc
        except httpx.TransportError as exc:
            raise _unreachable(exc) from exc
        except ijson.JSONError as exc:
            raise shape_error("Unparseable row stream.") from exc
        return out

    async def view(self, dataset_id: str) -> dict[str, Any]:
        view = await self.get_json(
            f"https://{PUBLIC_HOST}/api/views/{dataset_id}.json", dataset_id=dataset_id
        )
        # A 200 error object would otherwise read as a non-tabular asset and be cached as one.
        if (
            not isinstance(view, dict)
            or view.get("error")
            or not view.get("id")
            or not isinstance(view.get("columns", []), list)
        ):
            raise shape_error("Expected a dataset metadata object.")
        return view

    async def soql(
        self, dataset_id: str, params: dict[str, Any], *, timeout: float = UPSTREAM_TIMEOUT_SECONDS
    ) -> list[dict[str, Any]]:
        rows = await self.get_json(
            f"https://{PUBLIC_HOST}/resource/{dataset_id}.json",
            params,
            dataset_id=dataset_id,
            timeout=timeout,
        )
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise shape_error("Expected a JSON array of rows.")
        return rows


@dataclass
class RowStream:
    """Rows read from a SODA2 response, stopped early once enough were seen."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    more: bool = False


class _AsyncReader:
    """Adapt an httpx byte stream to the `read(n)` interface ijson expects, counting bytes."""

    def __init__(self, response: httpx.Response) -> None:
        self._chunks = response.aiter_bytes()
        self._buffer = b""
        self.total = 0

    async def _pull(self) -> bool:
        """Buffer one more chunk under the size cap; False at end of stream."""
        try:
            chunk = await anext(self._chunks)
        except StopAsyncIteration:
            return False
        self.total += len(chunk)
        if self.total > UPSTREAM_MAX_BYTES:
            raise _too_large()
        self._buffer += chunk
        return True

    async def first_byte(self) -> bytes:
        """The first non-whitespace byte, without consuming it."""
        while not self._buffer.lstrip():
            if not await self._pull():
                return b""
        return self._buffer.lstrip()[:1]

    async def read(self, size: int = -1) -> bytes:
        while size < 0 or len(self._buffer) < size:
            if not await self._pull():
                break
        if size < 0:
            out, self._buffer = self._buffer, b""
        else:
            out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out


def _header_list(headers: httpx.Headers, name: str) -> list[str]:
    try:
        value = json.loads(headers.get(name, "[]"))
    except json.JSONDecodeError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _endpoint(url: str) -> Literal["resource", "views", "catalog"]:
    if "/resource/" in url:
        return "resource"
    return "views" if "/api/views/" in url else "catalog"


def shape_error(reason: str) -> SeattleDataError:
    """An upstream 200 whose body is not the expected shape: never a successful empty result."""
    return SeattleDataError(
        ErrorCode.UPSTREAM_UNAVAILABLE,
        "The upstream service returned an unexpected response.",
        details=ErrorDetails(reason=reason),
    )


def _timeout(seconds: float) -> SeattleDataError:
    return SeattleDataError(
        ErrorCode.QUERY_TIMEOUT, f"The upstream service did not finish within {seconds:.0f} s."
    )


def _unreachable(exc: Exception) -> SeattleDataError:
    return SeattleDataError(
        ErrorCode.UPSTREAM_UNAVAILABLE,
        "The upstream service could not be reached.",
        details=ErrorDetails(reason=type(exc).__name__),
    )


def _too_large() -> SeattleDataError:
    return SeattleDataError(
        ErrorCode.UPSTREAM_UNAVAILABLE,
        "The upstream response exceeded the size limit.",
        details=ErrorDetails(reason=f"More than {UPSTREAM_MAX_BYTES} bytes."),
        temporary=False,
    )


async def _read_capped(response: httpx.Response) -> bytes:
    """Read the decoded body, stopping as soon as it passes the cap."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > UPSTREAM_MAX_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _reject_redirect(response: httpx.Response) -> None:
    if response.is_redirect:
        raise SeattleDataError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "The upstream service answered with an unexpected redirect.",
            details=ErrorDetails(reason=f"HTTP {response.status_code}."),
        )


def _parse_json(content: bytes) -> Any:
    try:
        return json.loads(content) if content else None
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
