from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository, SqlAuditRepository
from alos.config import Settings
from alos.contracts import CanonicalContractCatalog
from alos.dependencies import get_factory_orchestrator, get_skill_service
from alos.identity import Principal
from alos.main import create_app
from alos.registry import DecisionAuthority, InMemoryRegistryStore
from alos.skills import models as skill_models
from alos.skills.assignment import SkillAssignmentError, SkillAssignmentService
from alos.skills.registry import SkillRegistry
from alos.skills.service import SkillService

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def test_stale_internal_skill_response_models_are_removed() -> None:
    assert not hasattr(skill_models, "SkillDefinitionContract")
    assert not hasattr(skill_models, "SkillListResponse")
    assert not hasattr(skill_models, "SkillDetailResponse")
    assert not hasattr(skill_models, "SkillError")


def skill_payload(
    *,
    permissions: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "skill_id": "skill.delta.authority",
        "skill_version": "1.0.0",
        "name": "Authority delta",
        "description": "Exercise least-privilege assignment checks.",
        "input_schema_ref": "https://schemas.alos.dev/example/input.json",
        "output_schema_ref": "https://schemas.alos.dev/example/output.json",
        "required_tool_ids": [],
        "permission_refs": list(permissions),
        "scope_refs": list(scopes),
    }


def agent_payload(
    *,
    permissions: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "tenant_id": "tenant_delta",
        "organization_id": "organization_delta",
        "workspace_id": "workspace_delta",
        "agent_id": "agent.delta.authority",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_delta",
        "name": "Authority delta agent",
        "purpose": "Exercise least-privilege assignment checks.",
        "risk_level": "LOW",
        "capability_ids": ["capability.delta"],
        "skill_refs": [],
        "model_policy_ref": "model-policy.delta",
        "tool_ids": [],
        "permission_refs": list(permissions),
        "scope_refs": list(scopes),
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }


def principal() -> Principal:
    return Principal(
        actor_id="actor_delta",
        tenant_id="tenant_delta",
        organization_id="organization_delta",
        workspace_id="workspace_delta",
        permissions=frozenset({"permission.a", "permission.b"}),
        scopes=frozenset({"scope.x", "scope.y"}),
    )


async def activate(registry: object, payload: dict[str, object]) -> None:
    entry = await registry.register(  # type: ignore[attr-defined]
        payload,
        tenant_id="tenant_delta",
        organization_id="organization_delta",
        workspace_id="workspace_delta",
        actor_id="actor_delta",
        correlation_id="corr_delta_authority",
    )
    entry = await registry.approve(  # type: ignore[attr-defined]
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_it_delta",
        decision_id=f"decision.{entry.subject_id}",
        authority=DecisionAuthority.IT,
        correlation_id="corr_delta_authority",
    )
    await registry.activate(  # type: ignore[attr-defined]
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_release_delta",
        release_id=f"release.{entry.subject_id}",
        correlation_id="corr_delta_authority",
    )


async def assignment_service(
    *,
    skill_permissions: tuple[str, ...] = (),
    skill_scopes: tuple[str, ...] = (),
    agent_permissions: tuple[str, ...] = (),
    agent_scopes: tuple[str, ...] = ("scope.x",),
) -> tuple[SkillAssignmentService, InMemoryAuditRepository]:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    skills = SkillRegistry(contracts, audit)
    agents = AgentRegistry(contracts, audit)
    await activate(
        skills,
        skill_payload(permissions=skill_permissions, scopes=skill_scopes),
    )
    await activate(
        agents,
        agent_payload(permissions=agent_permissions, scopes=agent_scopes),
    )
    return SkillAssignmentService(registry=skills, agents=agents, audit=audit), audit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill_permissions", "agent_permissions", "expected_code"),
    [
        (("permission.b",), ("permission.a",), "SKILL_PERMISSION_MISMATCH"),
        (("permission.b",), ("permission.a", "permission.b"), None),
        ((), ("permission.a",), None),
    ],
)
async def test_assignment_never_expands_agent_permissions(
    skill_permissions: tuple[str, ...],
    agent_permissions: tuple[str, ...],
    expected_code: str | None,
) -> None:
    service, audit = await assignment_service(
        skill_permissions=skill_permissions,
        agent_permissions=agent_permissions,
    )
    if expected_code is None:
        receipt = await service.assign(
            agent_id="agent.delta.authority",
            agent_version="1.0.0",
            skill_id="skill.delta.authority",
            skill_version="1.0.0",
            proposed_agent_version="1.1.0",
            principal=principal(),
            correlation_id="corr_delta_authority",
        )
        assert receipt.lifecycle_state == "DRAFT"
        return
    with pytest.raises(SkillAssignmentError) as raised:
        await service.assign(
            agent_id="agent.delta.authority",
            agent_version="1.0.0",
            skill_id="skill.delta.authority",
            skill_version="1.0.0",
            proposed_agent_version="1.1.0",
            principal=principal(),
            correlation_id="corr_delta_authority",
        )
    assert raised.value.code == expected_code
    rejection = audit.list_events(tenant_id="tenant_delta")[0]
    assert rejection.event_type == "skill.assignment.rejected"
    assert rejection.correlation_id == "corr_delta_authority"
    assert rejection.actor_id == "actor_delta"
    assert rejection.organization_id == "organization_delta"
    assert rejection.workspace_id == "workspace_delta"
    assert rejection.metadata["skill_version"] == "1.0.0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill_scopes", "agent_scopes", "expected_code"),
    [
        (("scope.y",), ("scope.x",), "SKILL_SCOPE_MISMATCH"),
        (("scope.y",), ("scope.x", "scope.y"), None),
        ((), ("scope.x",), None),
    ],
)
async def test_assignment_never_expands_agent_scope(
    skill_scopes: tuple[str, ...],
    agent_scopes: tuple[str, ...],
    expected_code: str | None,
) -> None:
    service, _ = await assignment_service(
        skill_scopes=skill_scopes,
        agent_scopes=agent_scopes,
    )
    if expected_code is None:
        receipt = await service.assign(
            agent_id="agent.delta.authority",
            agent_version="1.0.0",
            skill_id="skill.delta.authority",
            skill_version="1.0.0",
            proposed_agent_version="1.1.0",
            principal=principal(),
            correlation_id="corr_delta_authority",
        )
        assert receipt.lifecycle_state == "DRAFT"
        return
    with pytest.raises(SkillAssignmentError) as raised:
        await service.assign(
            agent_id="agent.delta.authority",
            agent_version="1.0.0",
            skill_id="skill.delta.authority",
            skill_version="1.0.0",
            proposed_agent_version="1.1.0",
            principal=principal(),
            correlation_id="corr_delta_authority",
        )
    assert raised.value.code == expected_code


def test_application_composes_one_agent_registry_and_environment_audit() -> None:
    test_app = create_app(
        Settings(
            _env_file=None,
            APP_ENV="test",
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
        )
    )
    assert test_app.state.agent_registry is test_app.state.factory_agent_registry
    assert isinstance(test_app.state.skill_audit, InMemoryAuditRepository)

    production_app = create_app(
        Settings(
            _env_file=None,
            APP_ENV="production",
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
        )
    )
    assert production_app.state.agent_registry is production_app.state.factory_agent_registry
    assert isinstance(production_app.state.skill_audit, SqlAuditRepository)


def app_request(app: object) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/",
            "headers": [],
        }
    )


@pytest.mark.asyncio
async def test_dependency_resolution_order_cannot_change_agent_registry() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
    )
    skill_first = create_app(settings)
    first_request = app_request(skill_first)
    service = await get_skill_service(first_request)
    await get_factory_orchestrator(first_request, object())  # type: ignore[arg-type]
    assert service._agents is skill_first.state.agent_registry
    assert skill_first.state.agent_registry is skill_first.state.factory_agent_registry

    factory_first = create_app(settings)
    second_request = app_request(factory_first)
    await get_factory_orchestrator(second_request, object())  # type: ignore[arg-type]
    service = await get_skill_service(second_request)
    assert service._agents is factory_first.state.agent_registry
    assert factory_first.state.agent_registry is factory_first.state.factory_agent_registry


@pytest.mark.asyncio
async def test_agent_registry_survives_rehydrate_with_shared_store() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    store = InMemoryRegistryStore()
    first = AgentRegistry(contracts, audit, store)
    await activate(first, agent_payload())

    rehydrated = AgentRegistry(contracts, audit, store)
    await rehydrated.hydrate()
    entry = rehydrated.get(
        tenant_id="tenant_delta",
        workspace_id="workspace_delta",
        subject_id="agent.delta.authority",
        version="1.0.0",
    )

    assert entry.state.value == "ACTIVE"
    assert entry.payload["agent_id"] == "agent.delta.authority"


@pytest.mark.asyncio
async def test_minimal_skill_projection_omits_absent_optional_values() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    skills = SkillRegistry(contracts)
    agents = AgentRegistry(contracts, InMemoryAuditRepository())
    payload = skill_payload()
    await activate(skills, payload)
    service = SkillService(registry=skills, agents=agents)

    items = service.list_skills(principal=principal())
    detail = service.get_skill(
        principal=principal(), skill_id="skill.delta.authority", version="1.0.0"
    )

    assert "risk_level" not in items[0]
    assert "owner_actor_id" not in items[0]
    assert "risk_level" not in detail
    assert "owner_actor_id" not in detail
    contracts.validate(
        "https://schemas.alos.dev/v1/skill/skill-list-response.schema.json",
        {"skills": items, "correlation_id": "corr_delta_authority"},
    )
    contracts.validate(
        "https://schemas.alos.dev/v1/skill/skill-detail.schema.json",
        detail,
    )


@pytest.mark.asyncio
async def test_public_skill_endpoints_omit_absent_optional_values() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            APP_ENV="test",
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
        )
    )
    app.state.auth_service.register(
        {
            "email": "skill-delta@andara.local",
            "password": "StrongPass!123",
            "display_name": "Skill Delta",
            "tenant_id": "tenant_delta",
            "organization_id": "organization_delta",
            "workspace_id": "workspace_delta",
            "permissions": ["permission.a", "permission.b"],
            "scopes": ["scope.x", "scope.y"],
        }
    )
    token = app.state.auth_service.login(
        "skill-delta@andara.local", "StrongPass!123"
    )["access_token"]
    await activate(app.state.skill_registry, skill_payload())
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": "corr_delta_endpoint",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        listing = await client.get("/api/v1/skills", headers=headers)
        detail = await client.get(
            "/api/v1/skills/skill.delta.authority",
            headers=headers,
        )

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert "owner_actor_id" not in listing.json()["skills"][0]
    assert "risk_level" not in listing.json()["skills"][0]
    assert "owner_actor_id" not in detail.json()
    assert "risk_level" not in detail.json()
