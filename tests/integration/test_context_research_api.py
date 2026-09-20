from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from alos.config import Settings
from alos.dependencies import get_genesis_client
from alos.integrations import ExternalRetrievalError
from alos.main import create_app

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"


class ResearchGenesisStub:
    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    async def research(
        self,
        payload: Mapping[str, Any],
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        self.received = dict(payload)
        return {
            "correlation_id": correlation_id,
            "decision": "REQUEST_EXTERNAL_RESEARCH",
            "domain": payload["domain"],
            "selected_evidence_ids": [],
            "reasons": ["External evidence is required and authorized via Backend."],
            "retrieval": {
                "boundary": "BACKEND_TOOL_EXECUTOR",
                "tool_id": "research.external.retrieve",
                "instruction_authority": False,
                "permission_expansion": False,
                "scope_expansion": False,
            },
            "external_content_trust": "UNTRUSTED",
        }


async def _client_with_principal(
    *,
    permissions: list[str],
    scopes: list[str],
) -> tuple[httpx.AsyncClient, ResearchGenesisStub, str, FastAPI]:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
        GENESIS_BASE_URL="http://genesis.test",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
    )
    app = create_app(settings)
    genesis = ResearchGenesisStub()
    app.dependency_overrides[get_genesis_client] = lambda: genesis
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "research@andara.local",
            "password": "StrongPass!123",
            "display_name": "Research User",
            "tenant_id": "tenant_research",
            "organization_id": "org_research",
            "workspace_id": "workspace_research",
            "roles": ["DIVISION_MEMBER"],
            "permissions": permissions,
            "scopes": scopes,
            "data_scope": "PROJECT",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "research@andara.local", "password": "StrongPass!123"},
    )
    return client, genesis, str(login.json()["access_token"]), app


@pytest.mark.asyncio
async def test_context_and_domain_access_are_backend_authoritative_projections() -> None:
    client, _genesis, token, _app = await _client_with_principal(
        permissions=["research.request", "admin"],
        scopes=["research.technology"],
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": "corr_context_research_001",
    }
    try:
        context = await client.get("/api/v1/genesis/context-options", headers=headers)
        access = await client.get("/api/v1/research/domain-access", headers=headers)
    finally:
        await client.aclose()

    assert context.status_code == 200
    assert context.json()["status"] == "ACTIVE"
    assert context.json()["tenant_id"] == "tenant_research"
    assert context.json()["correlation_id"] == "corr_context_research_001"
    assert "permissions" not in context.json()

    assert access.status_code == 200
    by_domain = {item["domain"]: item for item in access.json()["domains"]}
    assert by_domain["TECHNOLOGY"]["status"] == "AUTHORIZED"
    assert by_domain["PROPERTY_MARKET"]["status"] == "DENIED"
    assert by_domain["PROPERTY_MARKET"]["is_allowed"] is False


@pytest.mark.asyncio
async def test_external_research_preserves_correlation_and_backend_authority() -> None:
    client, genesis, token, app = await _client_with_principal(
        permissions=["research.request", "research.external.read"],
        scopes=["research.technology", "scope.sources.external_read"],
    )
    try:
        response = await client.post(
            "/api/v1/research/requests",
            json={
                "question": "Apa teknologi yang relevan untuk otomasi operasional?",
                "source_mode": "EXTERNAL",
                "domain": "TECHNOLOGY",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": "corr_research_external_001",
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "NEEDS_REVIEW"
    assert body["correlation_id"] == "corr_research_external_001"
    assert body["decision"] == "REQUEST_EXTERNAL_RESEARCH"
    assert genesis.received is not None
    execution_context = genesis.received["execution_context"]
    assert execution_context["correlation_id"] == "corr_research_external_001"
    assert execution_context["allowed_tool_ids"] == ["research.external.retrieve"]
    assert execution_context["permission_refs"] == [
        "research.request",
        "research.external.read",
    ]
    assert execution_context["scope_refs"] == [
        "research.technology",
        "scope.sources.external_read",
    ]
    assert execution_context["authority_context"]["authority_level"] == "REQUESTER"
    assert execution_context["tenant_id"] == "tenant_research"
    assert execution_context["organization_id"] == "org_research"
    assert execution_context["workspace_id"] == "workspace_research"

    events = app.state.research_audit.list_events(
        tenant_id="tenant_research",
        workspace_id="workspace_research",
    )
    assert len(events) == 1
    assert events[0].outcome == "SUCCESS"
    assert events[0].correlation_id == "corr_research_external_001"
    assert events[0].metadata == {
        "domain": "TECHNOLOGY",
        "decision": "REQUEST_EXTERNAL_RESEARCH",
    }


@pytest.mark.asyncio
async def test_research_denial_is_fail_closed_before_genesis() -> None:
    client, genesis, token, app = await _client_with_principal(
        permissions=["research.request"],
        scopes=["research.technology"],
    )
    try:
        response = await client.post(
            "/api/v1/research/requests",
            json={
                "question": "Analisis pasar properti pada area yang diminta.",
                "source_mode": "INTERNAL",
                "domain": "PROPERTY_MARKET",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": "corr_research_denied_001",
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 403
    assert response.json()["code"] == "RESEARCH_SCOPE_DENIED"
    assert response.json()["correlation_id"] == "corr_research_denied_001"
    assert genesis.received is None
    events = app.state.research_audit.list_events(
        tenant_id="tenant_research",
        workspace_id="workspace_research",
    )
    assert len(events) == 1
    assert events[0].outcome == "DENIED"
    assert events[0].correlation_id == "corr_research_denied_001"
    assert events[0].metadata["decision"] == "SCOPE_DENIED"


@pytest.mark.asyncio
async def test_browser_preflight_allows_authorization_header() -> None:
    client, _genesis, _token, _app = await _client_with_principal(
        permissions=["research.request"],
        scopes=["research.technology"],
    )
    try:
        response = await client.options(
            "/api/v1/research/requests",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,x-correlation-id",
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "x-correlation-id" in allowed


@pytest.mark.asyncio
async def test_external_research_private_url_is_blocked_and_audited_incrementally() -> None:
    client, genesis, token, app = await _client_with_principal(
        permissions=["research.request", "research.external.read"],
        scopes=["research.technology", "scope.sources.external_read"],
    )
    correlation_id = "corr_shared_private_egress_001"
    try:
        decision = await client.post(
            "/api/v1/research/requests",
            json={
                "question": "Periksa sumber eksternal untuk teknologi operasional terbaru.",
                "source_mode": "EXTERNAL",
                "domain": "TECHNOLOGY",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": correlation_id,
            },
        )
        assert decision.status_code == 200
        assert decision.json()["decision"] == "REQUEST_EXTERNAL_RESEARCH"
        assert genesis.received is not None

        with pytest.raises(ExternalRetrievalError) as raised:
            await app.state.external_retrieval_service.retrieve(
                "https://127.0.0.1/private-evidence",
                tenant_id="tenant_research",
                organization_id="org_research",
                workspace_id="workspace_research",
                actor_id="actor_research",
                correlation_id=correlation_id,
                scope_refs=frozenset({"scope.sources.external_read"}),
            )
    finally:
        await client.aclose()

    assert raised.value.code == "EGRESS_PRIVATE_NETWORK_BLOCKED"
    research_events = app.state.research_audit.list_events(
        tenant_id="tenant_research",
        workspace_id="workspace_research",
    )
    retrieval_events = app.state.external_retrieval_audit.list_events(
        tenant_id="tenant_research",
        workspace_id="workspace_research",
    )
    assert research_events[0].correlation_id == correlation_id
    assert research_events[0].outcome == "SUCCESS"
    assert retrieval_events[0].correlation_id == correlation_id
    assert retrieval_events[0].outcome == "BLOCKED"
    assert retrieval_events[0].metadata["code"] == "EGRESS_PRIVATE_NETWORK_BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant_attacker"),
        ("workspace_id", "workspace_attacker"),
        ("scope_refs", ["research.property_market"]),
        ("permission_refs", ["research.external.read", "admin"]),
    ],
)
async def test_public_research_request_cannot_expand_backend_authority(
    field: str,
    value: object,
) -> None:
    client, genesis, token, _app = await _client_with_principal(
        permissions=["research.request"],
        scopes=["research.technology"],
    )
    correlation_id = f"corr_shared_injection_{field}"
    payload: dict[str, object] = {
        "question": "Gunakan hanya authority yang diterbitkan Backend untuk riset ini.",
        "source_mode": "INTERNAL",
        "domain": "TECHNOLOGY",
        field: value,
    }
    try:
        response = await client.post(
            "/api/v1/research/requests",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": correlation_id,
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["correlation_id"] == correlation_id
    assert genesis.received is None
