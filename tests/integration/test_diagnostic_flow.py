from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from alos.config import Settings
from alos.dependencies import get_genesis_client, get_integration_contract_validator
from alos.integrations.genesis import GenesisClient, IntegrationContractValidator
from alos.main import create_app

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def genesis_payload(correlation_id: str) -> dict[str, object]:
    return {
        "service": "genesis-ai",
        "status": "reachable",
        "role": "AI_CONTROL_PLANE",
        "authoritative_business_state": False,
        "provider_required": False,
        "correlation_id": correlation_id,
    }


async def call_backend(
    transport: httpx.AsyncBaseTransport,
    *,
    correlation_id: str | None = None,
) -> httpx.Response:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
        GENESIS_BASE_URL="http://genesis.test",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
    )
    app = create_app(settings)
    genesis_http = httpx.AsyncClient(transport=transport, base_url="http://genesis.test")
    genesis_client = GenesisClient(
        base_url="http://genesis.test",
        internal_token=SecretStr("test-only-token"),
        client=genesis_http,
    )

    async def override_genesis_client() -> AsyncIterator[GenesisClient]:
        yield genesis_client

    app.dependency_overrides[get_genesis_client] = override_genesis_client
    app.dependency_overrides[get_integration_contract_validator] = lambda: (
        IntegrationContractValidator(CONTRACTS_ROOT)
    )

    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
        ) as backend_http:
            return await backend_http.get("/api/v1/system/integration", headers=headers)
    finally:
        await genesis_http.aclose()


@pytest.mark.asyncio
async def test_backend_is_reachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_valid_genesis_response_is_returned_to_web() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        correlation_id = request.headers["X-Correlation-ID"]
        return httpx.Response(200, json=genesis_payload(correlation_id))

    response = await call_backend(httpx.MockTransport(handler))

    assert response.status_code == 200
    document = response.json()
    assert document["status"] == "connected"
    assert document["backend"]["authority"] == "ALOS_BACKEND"
    assert document["genesis"]["role"] == "AI_CONTROL_PLANE"
    assert document["correlation_id"].startswith("corr_")


@pytest.mark.asyncio
async def test_genesis_unavailable_returns_structured_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    response = await call_backend(httpx.MockTransport(handler))

    assert response.status_code == 503
    assert response.json()["code"] == "GENESIS_UNAVAILABLE"
    assert response.json()["retryable"] is True
    assert response.json()["correlation_id"] == response.headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_invalid_genesis_response_is_rejected() -> None:
    response = await call_backend(
        httpx.MockTransport(lambda _request: httpx.Response(200, json={"status": "reachable"}))
    )

    assert response.status_code == 502
    assert response.json()["code"] == "GENESIS_INVALID_RESPONSE"
    assert response.json()["details"]["path"] == "$"


@pytest.mark.asyncio
async def test_mismatched_genesis_correlation_is_rejected() -> None:
    response = await call_backend(
        httpx.MockTransport(
            lambda _request: httpx.Response(200, json=genesis_payload("corr_wrong_001"))
        ),
        correlation_id="corr_expected_001",
    )

    assert response.status_code == 502
    assert response.json()["code"] == "GENESIS_INVALID_RESPONSE"
    assert response.json()["details"]["path"] == "correlation_id"


@pytest.mark.asyncio
async def test_correlation_id_is_propagated_through_genesis_and_back() -> None:
    correlation_id = "corr_web_backend_genesis_001"
    seen_correlation: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_correlation
        seen_correlation = request.headers.get("X-Correlation-ID")
        return httpx.Response(200, json=genesis_payload(correlation_id))

    response = await call_backend(httpx.MockTransport(handler), correlation_id=correlation_id)

    assert response.status_code == 200
    assert seen_correlation == correlation_id
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json()["correlation_id"] == correlation_id
    assert response.json()["genesis"]["correlation_id"] == correlation_id
