import json
from pathlib import Path

import pytest

from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
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
    draft = await registry.register(
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

    service = SkillAssignmentService(registry=registry, audit=None)
    with pytest.raises(ValueError, match="ACTIVE|authorized|version"):
        await service.assign(
            agent_id="agent_summary",
            skill_id="research.summary",
            skill_version="1.0.0",
            principal=principal,
            correlation_id="corr_skill_002",
            agent_scope=frozenset({"scope.workspace.ops"}),
        )

    with pytest.raises(ValueError, match="version|authorize|Skill"):
        await service.assign(
            agent_id="agent_summary",
            skill_id="research.summary",
            skill_version="9.9.9",
            principal=principal,
            correlation_id="corr_skill_002",
            agent_scope=frozenset({"scope.workspace.ops"}),
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
        scopes=frozenset({"scope.workspace.ops"}),
    )
    audit = InMemoryAuditRepository()
    service = SkillAssignmentService(registry=registry, audit=audit)

    with pytest.raises(ValueError, match="scope"):
        await service.assign(
            agent_id="agent_summary",
            skill_id="research.summary",
            skill_version="1.0.0",
            principal=principal,
            correlation_id="corr_skill_003",
            agent_scope=frozenset({"scope.other"}),
        )

    events = audit.list_events(tenant_id="tenant_mvp1_andara", workspace_id="workspace_mvp1_ops")
    assert any(item.event_type == "skill.assignment.rejected" for item in events)
