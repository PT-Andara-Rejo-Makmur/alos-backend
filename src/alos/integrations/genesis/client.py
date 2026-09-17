"""Typed transport behavior for the GENESIS internal HTTP contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class GenesisClientError(Exception):
    code: str
    message: str
    correlation_id: str
    retryable: bool
    status_code: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message} (correlation_id={self.correlation_id})"


class GenesisClient:
    """GENESIS client that imports no genesis-ai implementation code."""

    def __init__(
        self,
        *,
        base_url: str,
        internal_token: SecretStr,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = internal_token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout_seconds)
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self, *, correlation_id: str) -> dict[str, Any]:
        return await self._request("GET", "/internal/v1/health", correlation_id=correlation_id)

    async def create_agent_run(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/v1/agent-runs", correlation_id=correlation_id, payload=payload
        )

    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/v1/factory/analyze", correlation_id=correlation_id, payload=payload
        )

    async def research(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/v1/research", correlation_id=correlation_id, payload=payload
        )

    async def review(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/v1/reviews", correlation_id=correlation_id, payload=payload
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        correlation_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._token.get_secret_value()
        headers = {"X-Correlation-ID": correlation_id}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self._client.request(
                method, path, headers=headers, json=dict(payload) if payload is not None else None
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GenesisClientError(
                code="GENESIS_TIMEOUT",
                message="GENESIS did not respond before the configured timeout.",
                correlation_id=correlation_id,
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise GenesisClientError(
                code="GENESIS_HTTP_ERROR",
                message="GENESIS returned a non-success response.",
                correlation_id=correlation_id,
                retryable=status_code >= 500,
                status_code=status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise GenesisClientError(
                code="GENESIS_UNAVAILABLE",
                message="GENESIS transport is unavailable.",
                correlation_id=correlation_id,
                retryable=True,
            ) from exc

        document = response.json()
        if not isinstance(document, dict):
            raise GenesisClientError(
                code="GENESIS_INVALID_RESPONSE",
                message="GENESIS response must be a JSON object.",
                correlation_id=correlation_id,
                retryable=False,
                status_code=response.status_code,
            )
        return document
