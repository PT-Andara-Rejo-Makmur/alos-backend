"""Integration tests for the frozen error contract (M2-H01-BE-02).

Internal failures must return a safe, typed error without SQL, stack traces,
schema details, credentials, or secrets.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from alos.config import Settings
from alos.dependencies import get_genesis_client
from alos.main import create_app

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"


class ExplodingGenesisStub:
    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        raise RuntimeError("SELECT * FROM capability_registry; password=hunter2 stack frame leaked")


async def authenticated_token(client: httpx.AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "errors@andara.local",
            "password": "StrongPass!123",
            "display_name": "Error Contract",
            "tenant_id": "tenant_errors",
            "organization_id": "org_errors",
            "workspace_id": "workspace_errors",
            "roles": ["DIVISION_MEMBER"],
            "permissions": ["reports.create"],
            "scopes": ["scope.workspace.errors"],
            "data_scope": "PROJECT",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "errors@andara.local", "password": "StrongPass!123"},
    )
    return str(login.json()["access_token"])


@pytest.mark.asyncio
async def test_internal_failure_returns_safe_typed_error() -> None:
    settings = Settings(
        APP_ENV="test",
        GENESIS_BASE_URL="http://genesis.internal",
        GENESIS_INTERNAL_TOKEN="internal-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
    )
    app = create_app(settings)
    app.dependency_overrides[get_genesis_client] = lambda: ExplodingGenesisStub()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )
    try:
        token = await authenticated_token(client)
        response = await client.post(
            "/api/v1/genesis/factory/analyze",
            json={"requirement": "Create a valid scoped weekly operational report."},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": "corr_error_contract_001",
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_PROCESSING_FAILURE"
    assert body["correlation_id"] == "corr_error_contract_001"
    assert body["retryable"] is False
    serialized = str(body)
    assert "SELECT" not in serialized
    assert "hunter2" not in serialized
    assert "capability_registry" not in serialized
    assert "RuntimeError" not in serialized
    assert body.get("details") is None
