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


class FactoryGenesisStub:
    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        self.received = dict(payload)
        requirement = payload["requirement"]
        assert isinstance(requirement, Mapping)
        context = requirement["execution_context"]
        assert isinstance(context, Mapping)
        draft = {
            "tenant_id": context["tenant_id"],
            "organization_id": context["organization_id"],
            "workspace_id": context["workspace_id"],
            "correlation_id": correlation_id,
            "capability_id": "capability_public_factory",
            "version": "0.1.0",
            "name": "Public Factory report",
            "purpose": requirement["statement"],
            "owner": context["actor_id"],
            "capability_type": "REPORT",
            "output_state": "DRAFT",
            "lifecycle_state": "DRAFT",
            "scope_refs": ["scope.workspace.operations"],
            "tool_ids": ["report.render"],
            "permission_refs": ["reports.create"],
            "prohibited_actions": ["Do not approve or release the proposal."],
            "risk_level": "LOW",
            "evidence_requirements": ["Cite authorized operational records."],
            "test_requirements": ["Validate the report against a fixture."],
        }
        return {
            "correlation_id": correlation_id,
            "resolution": {
                "understanding": {
                    "normalized_intent": requirement["statement"],
                    "domains": ["operations"],
                    "candidate_capability_ids": [],
                    "recommended_type": "REPORT",
                    "risk_level": "LOW",
                    "requires_agent": False,
                    "rationale": ["A report capability is sufficient."],
                },
                "decision": "CREATE",
                "reason": "No authorized matching capability exists.",
                "purpose": requirement["statement"],
                "scope_refs": ["scope.workspace.operations"],
                "resolved": [],
                "missing_capability_ids": ["capability_public_factory"],
                "required_tool_ids": ["report.render"],
                "required_permission_refs": ["reports.create"],
                "evidence_requirements": ["Cite authorized operational records."],
                "test_requirements": ["Validate the report against a fixture."],
                "activation_readiness": "READY_FOR_DRAFT",
            },
            "existing_capability_refs": [],
            "capability_draft": draft,
            "agent_draft": None,
            "missing_dependencies": ["capability_public_factory"],
            "handoff": {
                "target_service": "alos-backend",
                "transport": "typed_internal_api",
                "requested_operations": ["REGISTER_CAPABILITY_DRAFT"],
                "authoritative_state_changed": False,
            },
        }


async def authenticated_client() -> tuple[httpx.AsyncClient, FactoryGenesisStub, str]:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
        GENESIS_BASE_URL="http://genesis.test",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
    )
    app = create_app(settings)
    genesis = FactoryGenesisStub()
    app.dependency_overrides[get_genesis_client] = lambda: genesis
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "factory@andara.local",
            "password": "StrongPass!123",
            "display_name": "Factory Requester",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "roles": ["DIVISION_MEMBER"],
            "permissions": ["reports.create", "admin.all"],
            "scopes": ["scope.workspace.operations"],
            "data_scope": "PROJECT",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "factory@andara.local", "password": "StrongPass!123"},
    )
    return client, genesis, str(login.json()["access_token"])


@pytest.mark.asyncio
async def test_public_factory_api_derives_authority_and_returns_registered_draft() -> None:
    client, genesis, token = await authenticated_client()
    try:
        response = await client.post(
            "/api/v1/genesis/factory/analyze",
            json={"requirement": "Create a weekly operational report with traceable evidence."},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": "corr_public_factory_001",
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["correlation_id"] == "corr_public_factory_001"
    assert body["decision"] == "CREATE"
    assert body["capability_draft"]["lifecycle_state"] == "DRAFT"
    assert body["registry_result"]["state"] == "DRAFT"
    assert genesis.received is not None
    context = genesis.received["requirement"]["execution_context"]
    assert context["tenant_id"] == "tenant_default"
    assert context["authority_context"]["authority_level"] == "REQUESTER"
    assert context["correlation_id"] == "corr_public_factory_001"


@pytest.mark.asyncio
async def test_public_factory_api_requires_auth_and_rejects_non_contract_context() -> None:
    client, _genesis, token = await authenticated_client()
    try:
        unauthenticated = await client.post(
            "/api/v1/genesis/factory/analyze",
            json={"requirement": "Create a valid scoped weekly operational report."},
        )
        injected_context = await client.post(
            "/api/v1/genesis/factory/analyze",
            json={
                "requirement": "Create a valid scoped weekly operational report.",
                "execution_context": {"authority_level": "DIRECTOR_APPROVER"},
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": "corr_public_factory_002",
            },
        )
    finally:
        await client.aclose()

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "MISSING_TOKEN"
    assert injected_context.status_code == 422
    assert injected_context.json()["code"] == "FACTORY_REQUEST_INVALID"
    assert injected_context.json()["correlation_id"] == "corr_public_factory_002"
