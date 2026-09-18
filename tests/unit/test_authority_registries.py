import json
from pathlib import Path
from typing import Any

import pytest

from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.capabilities.registry import CapabilityRegistry
from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.registry import DecisionAuthority, RegistryConflictError, RegistryState
from alos.skills.registry import SkillRegistry

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"
MVP1_FIXTURES = CONTRACTS_ROOT / "compatibility" / "fixtures" / "mvp1"


def load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((MVP1_FIXTURES / name).read_text(encoding="utf-8"))
    return {key: value for key, value in payload.items() if key != "$schema"}


@pytest.fixture()
def catalog() -> CanonicalContractCatalog:
    return CanonicalContractCatalog(CONTRACTS_ROOT)


@pytest.mark.asyncio
async def test_agent_registry_validates_and_activates_authoritative_definition(
    catalog: CanonicalContractCatalog,
) -> None:
    audit = InMemoryAuditRepository()
    registry = AgentRegistry(catalog, audit)
    payload = load_fixture("agent-definition.adapted.json")

    draft = await registry.register(
        payload,
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_mvp1_contract_001",
    )
    approved = await registry.approve(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        subject_id=draft.subject_id,
        version=draft.version,
        actor_id="actor_mvp1_it_lead",
        decision_id="decision_mvp1_it_001",
        authority=DecisionAuthority.IT,
        correlation_id="corr_mvp1_contract_001",
    )
    active = await registry.activate(
        tenant_id=approved.tenant_id,
        workspace_id=approved.workspace_id,
        subject_id=approved.subject_id,
        version=approved.version,
        actor_id="actor_mvp1_it_lead",
        release_id="release_mvp1_agent_001",
        correlation_id="corr_mvp1_contract_001",
    )

    assert draft.state == RegistryState.DRAFT
    assert active.state == RegistryState.ACTIVE
    assert active.decision_id == "decision_mvp1_it_001"
    assert active.release_id == "release_mvp1_agent_001"
    assert len(audit.list_events(tenant_id=draft.tenant_id)) == 3

    draft.payload["name"] = "Caller mutation"
    stored = registry.get(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        subject_id=draft.subject_id,
        version=draft.version,
    )
    assert stored.payload["name"] != "Caller mutation"


@pytest.mark.asyncio
async def test_registry_rejects_invalid_contract_and_ai_approval(
    catalog: CanonicalContractCatalog,
) -> None:
    registry = AgentRegistry(catalog, InMemoryAuditRepository())
    payload = load_fixture("agent-definition.adapted.json")
    invalid = dict(payload)
    invalid["agent_id"] = "contains spaces"

    with pytest.raises(ContractValidationError):
        await registry.register(
            invalid,
            tenant_id="tenant_mvp1_andara",
            organization_id="org_mvp1_andara",
            workspace_id="workspace_mvp1_ops",
            actor_id="actor_mvp1_it_lead",
            correlation_id="corr_mvp1_contract_001",
        )

    draft = await registry.register(
        payload,
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_mvp1_contract_001",
    )
    with pytest.raises(RegistryConflictError, match="AI recommendation"):
        await registry.approve(
            tenant_id=draft.tenant_id,
            workspace_id=draft.workspace_id,
            subject_id=draft.subject_id,
            version=draft.version,
            actor_id="agent_genesis",
            decision_id="recommendation_not_decision",
            authority="AI",  # type: ignore[arg-type]
            correlation_id="corr_mvp1_contract_001",
        )


@pytest.mark.asyncio
async def test_capability_and_skill_registries_use_canonical_contracts(
    catalog: CanonicalContractCatalog,
) -> None:
    audit = InMemoryAuditRepository()
    capability_registry = CapabilityRegistry(catalog, audit)
    capability = await capability_registry.register(
        load_fixture("capability-definition.adapted.json"),
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_mvp1_contract_001",
    )
    skill_registry = SkillRegistry(catalog, audit)
    skill = await skill_registry.register(
        {
            "skill_id": "skill_document_summary",
            "skill_version": "1.0.0",
            "name": "Document summary",
            "description": "Summarize authorized context.",
            "purpose": "Produce a concise draft summary.",
            "when_to_use": ["An authorized document requires a reviewable summary"],
            "input_schema_ref": "https://schemas.alos.dev/example/input.json",
            "output_schema_ref": "https://schemas.alos.dev/example/output.json",
            "procedure": ["Read authorized context", "Cite evidence", "Return a draft"],
            "restrictions": ["No direct database access", "No self-approval"],
        },
        tenant_id="tenant_mvp1_andara",
        organization_id="org_mvp1_andara",
        workspace_id="workspace_mvp1_ops",
        actor_id="actor_mvp1_it_lead",
        correlation_id="corr_mvp1_contract_001",
    )

    assert capability.subject_type == "capability"
    assert skill.subject_type == "skill"
    assert skill.state == RegistryState.DRAFT
