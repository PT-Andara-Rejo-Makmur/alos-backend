"""Integration tests for the authoritative CapabilityDetail endpoint (M2-H01-BE-02)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from alos.config import Settings
from alos.dependencies import get_genesis_client
from alos.main import create_app
from alos.registry import DecisionAuthority

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"

CAPABILITY_ID = "capability_detail_report"
DRAFT_PERMISSIONS = ["reports.create"]
DRAFT_SCOPES = ["scope.workspace.operations"]


class DetailGenesisStub:
    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        requirement = payload["requirement"]
        assert isinstance(requirement, Mapping)
        context = requirement["execution_context"]
        assert isinstance(context, Mapping)
        return {
            "correlation_id": correlation_id,
            "resolution": {
                "understanding": {
                    "requirement_id": requirement["requirement_id"],
                    "ambiguity": "NONE",
                    "normalized_intent": "Create an evidence-backed operational report.",
                    "domains": ["operations"],
                    "candidate_capability_ids": [],
                    "recommended_type": "REPORT",
                    "risk_level": "LOW",
                    "requires_agent": False,
                    "rationale": ["A report capability is sufficient."],
                },
                "decision": "CREATE",
                "reason": "No authorized matching capability exists.",
                "purpose": "Create an evidence-backed operational report.",
                "scope_refs": DRAFT_SCOPES,
                "resolved": [],
                "missing_capability_ids": [CAPABILITY_ID],
                "required_tool_ids": ["report.render"],
                "required_permission_refs": DRAFT_PERMISSIONS,
                "evidence_requirements": ["Cite authorized operational records."],
                "test_requirements": ["Validate the report against a fixture."],
                "activation_readiness": "READY_FOR_DRAFT",
                "human_gate_required": True,
            },
            "existing_capability_refs": [],
            "capability_draft": {
                "tenant_id": context["tenant_id"],
                "organization_id": context["organization_id"],
                "workspace_id": context["workspace_id"],
                "correlation_id": correlation_id,
                "capability_id": CAPABILITY_ID,
                "version": "0.1.0",
                "name": "Detail report",
                "purpose": "Create an evidence-backed operational report.",
                "owner": context["actor_id"],
                "capability_type": "REPORT",
                "output_state": "DRAFT",
                "lifecycle_state": "DRAFT",
                "scope_refs": DRAFT_SCOPES,
                "tool_ids": ["report.render"],
                "permission_refs": DRAFT_PERMISSIONS,
                "prohibited_actions": ["Do not approve or release this proposal."],
                "risk_level": "LOW",
                "evidence_requirements": ["Cite authorized operational records."],
                "test_requirements": ["Validate the report against a fixture."],
                "human_gate_required": True,
                "dependency_refs": ["capability_existing_report"],
            },
            "agent_draft": None,
            "missing_dependencies": [CAPABILITY_ID],
            "handoff": {
                "target_service": "alos-backend",
                "transport": "typed_internal_api",
                "requested_operations": ["REGISTER_CAPABILITY_DRAFT"],
                "authoritative_state_changed": False,
            },
        }


async def register_and_login(
    client: httpx.AsyncClient,
    email: str,
    *,
    permissions: list[str],
    role: str = "DIVISION_MEMBER",
) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": email.split("@")[0],
            "tenant_id": "tenant_detail",
            "organization_id": "org_detail",
            "workspace_id": "workspace_detail",
            "roles": [role],
            "permissions": permissions,
            "scopes": DRAFT_SCOPES,
            "data_scope": "PROJECT",
        },
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "StrongPass!123"}
    )
    return str(login.json()["access_token"])


async def build_workspace() -> tuple[httpx.AsyncClient, DetailGenesisStub, str, str, str, str]:
    settings = Settings(
        APP_ENV="test",
        GENESIS_BASE_URL="http://genesis.internal",
        GENESIS_INTERNAL_TOKEN="internal-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
    )
    app = create_app(settings)
    genesis = DetailGenesisStub()
    app.dependency_overrides[get_genesis_client] = lambda: genesis
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    owner_token = await register_and_login(
        client, "owner@detail.local", permissions=DRAFT_PERMISSIONS
    )
    peer_token = await register_and_login(
        client, "peer@detail.local", permissions=DRAFT_PERMISSIONS
    )
    restricted_token = await register_and_login(
        client,
        "restricted@detail.local",
        permissions=[],
        role="BUSINESS_REVIEWER",
    )
    await client.post(
        "/api/v1/genesis/factory/analyze",
        json={"requirement": "Create a weekly operational report with traceable evidence."},
        headers={
            "Authorization": f"Bearer {owner_token}",
            "X-Correlation-ID": "corr_detail_001",
        },
    )
    return client, genesis, owner_token, peer_token, restricted_token, app


def detail_headers(token: str, correlation_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Correlation-ID": correlation_id}


@pytest.mark.asyncio
async def test_owner_reads_typed_draft_detail_without_internal_details() -> None:
    client, _genesis, owner_token, *_rest = await build_workspace()
    try:
        response = await client.get(
            f"/api/v1/capabilities/{CAPABILITY_ID}",
            headers=detail_headers(owner_token, "corr_detail_010"),
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["capability_id"] == CAPABILITY_ID
    assert body["version"] == "0.1.0"
    assert body["lifecycle_state"] == "DRAFT"
    assert body["human_gate_required"] is True
    assert body["availability"] == "UNAVAILABLE"
    assert body["configuration_status"] == "NEEDS_CONFIGURATION"
    assert body["correlation_id"] == "corr_detail_001"
    assert body["prohibited_actions"] == ["Do not approve or release this proposal."]
    assert body["dependency_refs"] == ["capability_existing_report"]
    assert body["owner"] == body["created_by"]
    assert body["created_by"].startswith("actor_")
    forbidden = {"sql", "query", "table", "columns", "connection", "row"}
    assert not forbidden & {key.lower() for key in body}


@pytest.mark.asyncio
async def test_draft_detail_is_private_and_states_fail_closed() -> None:
    client, _genesis, owner_token, peer_token, _restricted, _app = await build_workspace()
    try:
        peer = await client.get(
            f"/api/v1/capabilities/{CAPABILITY_ID}",
            headers=detail_headers(peer_token, "corr_detail_011"),
        )
        unknown = await client.get(
            "/api/v1/capabilities/capability_missing",
            headers=detail_headers(owner_token, "corr_detail_012"),
        )
        unauthenticated = await client.get(f"/api/v1/capabilities/{CAPABILITY_ID}")
    finally:
        await client.aclose()

    assert peer.status_code == 404
    assert peer.json()["code"] == "CAPABILITY_NOT_FOUND"
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "CAPABILITY_NOT_FOUND"
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "MISSING_TOKEN"


@pytest.mark.asyncio
async def test_approved_state_fails_closed_until_release_activates() -> None:
    client, _genesis, owner_token, peer_token, _restricted, app = await build_workspace()
    try:
        registry = app.state.factory_capability_registry
        await registry.approve(
            tenant_id="tenant_detail",
            workspace_id="workspace_detail",
            subject_id=CAPABILITY_ID,
            version="0.1.0",
            actor_id="actor_it_approver",
            decision_id="decision_detail_001",
            authority=DecisionAuthority.IT,
            correlation_id="corr_detail_020",
        )
        approved = await client.get(
            f"/api/v1/capabilities/{CAPABILITY_ID}",
            headers=detail_headers(owner_token, "corr_detail_021"),
        )
        assert approved.status_code == 404
        assert approved.json()["code"] == "CAPABILITY_NOT_FOUND"

        await registry.activate(
            tenant_id="tenant_detail",
            workspace_id="workspace_detail",
            subject_id=CAPABILITY_ID,
            version="0.1.0",
            actor_id="actor_release_authority",
            release_id="release_detail_001",
            correlation_id="corr_detail_022",
        )
        owner_active = await client.get(
            f"/api/v1/capabilities/{CAPABILITY_ID}",
            headers=detail_headers(owner_token, "corr_detail_023"),
        )
        peer_active = await client.get(
            f"/api/v1/capabilities/{CAPABILITY_ID}",
            headers=detail_headers(peer_token, "corr_detail_024"),
        )
    finally:
        await client.aclose()

    assert owner_active.status_code == 200
    body = owner_active.json()
    assert body["lifecycle_state"] == "ACTIVE"
    assert body["availability"] == "UNAVAILABLE"
    assert body["decision_id"] == "decision_detail_001"
    assert body["release_id"] == "release_detail_001"
    assert peer_active.status_code == 200
    assert peer_active.json()["lifecycle_state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_active_detail_requires_registry_authority() -> None:
    client, _genesis, _owner_token, _peer, restricted_token, app = await build_workspace()
    try:
        registry = app.state.factory_capability_registry
        await registry.approve(
            tenant_id="tenant_detail",
            workspace_id="workspace_detail",
            subject_id=CAPABILITY_ID,
            version="0.1.0",
            actor_id="actor_it_approver",
            decision_id="decision_detail_002",
            authority=DecisionAuthority.IT,
            correlation_id="corr_detail_030",
        )
        await registry.activate(
            tenant_id="tenant_detail",
            workspace_id="workspace_detail",
            subject_id=CAPABILITY_ID,
            version="0.1.0",
            actor_id="actor_release_authority",
            release_id="release_detail_002",
            correlation_id="corr_detail_031",
        )
        restricted = await client.get(
            f"/api/v1/capabilities/{CAPABILITY_ID}",
            headers=detail_headers(restricted_token, "corr_detail_032"),
        )
    finally:
        await client.aclose()

    assert restricted.status_code == 403
    assert restricted.json()["code"] == "CAPABILITY_NOT_AUTHORIZED"