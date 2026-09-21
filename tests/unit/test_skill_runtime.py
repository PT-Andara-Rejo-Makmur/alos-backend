from pathlib import Path

import pytest

from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
from alos.registry import InMemoryRegistryStore
from alos.skills.assignment import SkillAssignmentService
from alos.skills.registry import SkillRegistry

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"


def load_skill_payload() -> dict[str, object]:
    return {
        "skill_id": "research.summary",
        "skill_version": "1.0.0",
        "name": "Research Summary",
        "description": "Summarize approved evidence.",
        "purpose": "Produce a concise summary from authorized sources.",
        "when_to_use": ["When an analysis needs a brief evidence-backed summary."],
        "input_schema_ref": "https://schemas.alos.dev/example/input.json",
        "output_schema_ref": "https://schemas.alos.dev/example/output.json",
        "procedure": ["Read approved context", "Cite evidence", "Return summary"],
        "tool_ids": ["tool.document.read"],
        "required_tool_ids": ["tool.document.read"],
        "owner_actor_id": "actor_mvp1_it_lead",
        "risk_level": "MEDIUM",
        "permission_refs": ["permission.document.read"],
        "scope_refs": ["scope.workspace.ops"],
        "evidence_requirements": ["Cited evidence required"],
        "restrictions": ["No direct database access"],
        "failure_modes": ["Insufficient evidence"],
        "escalation": ["Escalate for human review"],
        "evaluation": ["Check citations"],
    }


def load_agent_payload() -> dict[str, object]:
    return {
        "tenant_id": "tenant_mvp1_andara",
        "organization_id": "org_mvp1_andara",
        "workspace_id": "workspace_mvp1_ops",
        "agent_id": "agent_summary",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_mvp1_it_lead",
        "name": "Summary Agent",
        "purpose": "Summarize authorized evidence.",
        "risk_level": "MEDIUM",
        "capability_ids": ["capability_summary"],
        "skill_refs": [],
        "model_policy_ref": "model-policy.standard-reviewed",
        "tool_ids": ["tool.document.read"],
        "permission_refs": ["permission.document.read"],
        "scope_refs": ["scope.workspace.ops"],
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }


async def activate_agent(registry: AgentRegistry) -> None:
    draft = await registry.register(
        load_agent_payload(),
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_agent_001",
    )
    approved = await registry.approve(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        subject_id=draft.subject_id,
        version=draft.version,
        actor_id="actor_mvp1_it_lead",
        decision_id="decision_agent_001",
        authority="IT",
        correlation_id="corr_agent_001",
    )
    await registry.activate(
        tenant_id=approved.tenant_id,
        workspace_id=approved.workspace_id,
        subject_id=approved.subject_id,
        version=approved.version,
        actor_id="actor_mvp1_it_lead",
        release_id="release_agent_001",
        correlation_id="corr_agent_001",
    )


@pytest.mark.asyncio
async def test_skill_registry_approves_and_authorizes_active_skill() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    registry = SkillRegistry(catalog, None)
    payload = load_skill_payload()
    draft = await registry.register(
        payload,
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_skill_001",
    )
    approved = await registry.approve(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        subject_id=draft.subject_id,
        version=draft.version,
        actor_id="actor_mvp1_it_lead",
        decision_id="decision_skill_001",
        authority="IT",  # type: ignore[arg-type]
        correlation_id="corr_skill_001",
    )
    active = await registry.activate(
        tenant_id=approved.tenant_id,
        workspace_id=approved.workspace_id,
        subject_id=approved.subject_id,
        version=approved.version,
        actor_id="actor_mvp1_it_lead",
        release_id="release_skill_001",
        correlation_id="corr_skill_001",
    )

    principal = Principal(
        actor_id="actor_mvp1_it_lead",
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        permissions=frozenset({"permission.document.read"}),
        scopes=frozenset({"scope.workspace.ops"}),
    )
    authorized = registry.get_authorized(
        principal=principal,
        subject_id=active.subject_id,
        version=active.version,
    )

    assert active.state.value == "ACTIVE"
    assert authorized.lifecycle == "ACTIVE"
    assert authorized.subject_id == "research.summary"


@pytest.mark.asyncio
async def test_skill_assignment_rejects_inactive_or_unauthorized_version() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    registry = SkillRegistry(catalog, None)
    payload = load_skill_payload()
    await registry.register(
        payload,
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_skill_002",
    )
    principal = Principal(
        actor_id="actor_mvp1_it_lead",
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        permissions=frozenset({"permission.document.read"}),
        scopes=frozenset({"scope.workspace.ops"}),
    )

    agents = AgentRegistry(catalog, InMemoryAuditRepository())
    await activate_agent(agents)
    service = SkillAssignmentService(registry=registry, agents=agents, audit=None)
    with pytest.raises(ValueError, match=r"ACTIVE|authorized|version"):
        await service.assign(
            agent_id="agent_summary",
            agent_version="1.0.0",
            skill_id="research.summary",
            skill_version="1.0.0",
            proposed_agent_version=None,
            principal=principal,
            correlation_id="corr_skill_002",
        )

    with pytest.raises(ValueError, match=r"version|authorize|Skill"):
        await service.assign(
            agent_id="agent_summary",
            agent_version="1.0.0",
            skill_id="research.summary",
            skill_version="9.9.9",
            proposed_agent_version=None,
            principal=principal,
            correlation_id="corr_skill_002",
        )


@pytest.mark.asyncio
async def test_skill_assignment_rejects_scope_mismatch_and_records_audit() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    registry = SkillRegistry(catalog, None)
    payload = load_skill_payload()
    draft = await registry.register(
        payload,
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_skill_003",
    )
    approved = await registry.approve(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        subject_id=draft.subject_id,
        version=draft.version,
        actor_id="actor_mvp1_it_lead",
        decision_id="decision_skill_003",
        authority="IT",  # type: ignore[arg-type]
        correlation_id="corr_skill_003",
    )
    await registry.activate(
        tenant_id=approved.tenant_id,
        workspace_id=approved.workspace_id,
        subject_id=approved.subject_id,
        version=approved.version,
        actor_id="actor_mvp1_it_lead",
        release_id="release_skill_003",
        correlation_id="corr_skill_003",
    )

    principal = Principal(
        actor_id="actor_mvp1_it_lead",
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        permissions=frozenset({"permission.document.read"}),
        scopes=frozenset({"scope.other"}),
    )
    audit = InMemoryAuditRepository()
    agents = AgentRegistry(catalog, audit)
    await activate_agent(agents)
    service = SkillAssignmentService(registry=registry, agents=agents, audit=audit)

    with pytest.raises(ValueError, match=r"ACTIVE|authorized"):
        await service.assign(
            agent_id="agent_summary",
            agent_version="1.0.0",
            skill_id="research.summary",
            skill_version="1.0.0",
            proposed_agent_version=None,
            principal=principal,
            correlation_id="corr_skill_003",
        )

    events = audit.list_events(tenant_id="tenant_mvp1_andara", workspace_id="workspace_mvp1_ops")
    assert any(item.event_type == "skill.assignment.rejected" for item in events)


@pytest.mark.asyncio
async def test_assignment_creates_new_agent_draft_as_only_source_of_truth() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    skills = SkillRegistry(catalog)
    agents = AgentRegistry(catalog, InMemoryAuditRepository())
    skill_draft = await skills.register(
        load_skill_payload(),
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_assign_001",
    )
    skill_approved = await skills.approve(
        tenant_id=skill_draft.tenant_id,
        workspace_id=skill_draft.workspace_id,
        subject_id=skill_draft.subject_id,
        version=skill_draft.version,
        actor_id="actor_mvp1_it_lead",
        decision_id="decision_skill_004",
        authority="IT",
        correlation_id="corr_assign_001",
    )
    await skills.activate(
        tenant_id=skill_approved.tenant_id,
        workspace_id=skill_approved.workspace_id,
        subject_id=skill_approved.subject_id,
        version=skill_approved.version,
        actor_id="actor_mvp1_it_lead",
        release_id="release_skill_004",
        correlation_id="corr_assign_001",
    )
    await activate_agent(agents)
    principal = Principal(
        actor_id="actor_mvp1_it_lead",
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        permissions=frozenset({"permission.document.read"}),
        scopes=frozenset({"scope.workspace.ops"}),
    )
    receipt = await SkillAssignmentService(registry=skills, agents=agents).assign(
        agent_id="agent_summary",
        agent_version="1.0.0",
        skill_id="research.summary",
        skill_version="1.0.0",
        proposed_agent_version="1.1.0",
        principal=principal,
        correlation_id="corr_assign_001",
    )
    draft = agents.get(
        tenant_id=principal.tenant_id,
        workspace_id=principal.workspace_id,
        subject_id="agent_summary",
        version="1.1.0",
    )
    assert receipt.lifecycle_state == "DRAFT"
    assert draft.state.value == "DRAFT"
    assert draft.payload["skill_refs"] == [
        {"skill_id": "research.summary", "skill_version": "1.0.0"}
    ]


@pytest.mark.asyncio
async def test_registry_rehydrates_persisted_state_after_restart() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    store = InMemoryRegistryStore()
    first = SkillRegistry(catalog, store=store)
    await first.register(
        load_skill_payload(),
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_restart_001",
    )
    restarted = SkillRegistry(catalog, store=store)
    await restarted.hydrate()
    restored = restarted.get(
        tenant_id="tenant_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        subject_id="research.summary",
        version="1.0.0",
    )
    assert restored.state.value == "DRAFT"
    assert restored.correlation_id == "corr_restart_001"


@pytest.mark.asyncio
async def test_registry_orders_versions_by_semver_not_lexically() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    registry = SkillRegistry(catalog)
    for version in ("1.10.0", "1.9.0"):
        payload = load_skill_payload()
        payload["skill_version"] = version
        await registry.register(
            payload,
            tenant_id="tenant_mvp1_andara",
            organization_id="org_mvp1_andara",
            workspace_id="workspace_mvp1_ops",
            actor_id="actor_mvp1_it_lead",
            correlation_id="corr_semver_001",
        )
    assert registry.versions(
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        subject_id="research.summary",
    ) == ("1.9.0", "1.10.0")


@pytest.mark.asyncio
async def test_registry_orders_semver_prereleases_before_release() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    registry = SkillRegistry(catalog)
    versions = ("1.0.0", "1.0.0-alpha.2", "1.0.0-alpha", "1.0.0-alpha.1")
    for version in versions:
        payload = load_skill_payload()
        payload["skill_version"] = version
        await registry.register(
            payload,
            tenant_id="tenant_mvp1_andara",
            organization_id="org_mvp1_andara",
            workspace_id="workspace_mvp1_ops",
            actor_id="actor_mvp1_it_lead",
            correlation_id="corr_semver_prerelease",
        )
    assert registry.versions(
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        subject_id="research.summary",
    ) == ("1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.2", "1.0.0")
