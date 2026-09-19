"""Negative authority tests for the frozen MVP2 Factory flow (M2-H01-BE-02).

Every fabricated authority (permission, scope, lifecycle, human gate, linkage)
must fail closed without creating registry state.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.capabilities.registry import CapabilityRegistry
from alos.contracts import CanonicalContractCatalog
from alos.factory import FactoryOrchestrator
from alos.identity import Principal
from alos.registry import DecisionAuthority, RegistryConflictError
from alos.security.errors import PlatformError

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"


class MutableStub:
    """GENESIS stub whose proposal can be mutated to simulate fabricated authority."""

    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides
        self.request: dict[str, Any] | None = None

    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        self.request = dict(payload)
        requirement = payload["requirement"]
        assert isinstance(requirement, Mapping)
        context = requirement["execution_context"]
        assert isinstance(context, Mapping)
        resolution_overrides = dict(self.overrides.get("resolution", {}))
        understanding_overrides = dict(self.overrides.get("understanding", {}))
        draft_overrides = dict(self.overrides.get("draft", {}))
        return {
            "correlation_id": correlation_id,
            "resolution": {
                "understanding": {
                    "requirement_id": requirement["requirement_id"],
                    "normalized_intent": "Create an evidence-backed operational report.",
                    "domains": ["operations"],
                    "candidate_capability_ids": [],
                    "recommended_type": "REPORT",
                    "risk_level": "LOW",
                    "requires_agent": False,
                    "rationale": ["A report capability is sufficient."],
                    **understanding_overrides,
                },
                "decision": "CREATE",
                "reason": "No authorized matching capability exists.",
                "purpose": "Create an evidence-backed operational report.",
                "scope_refs": ["scope.workspace.factory"],
                "resolved": [],
                "missing_capability_ids": ["capability_negative_report"],
                "required_tool_ids": ["report.render"],
                "required_permission_refs": ["permission.report.create"],
                "evidence_requirements": ["Reference the source records used by the report."],
                "test_requirements": ["Verify the report against an authorized fixture."],
                "activation_readiness": "READY_FOR_DRAFT",
                "human_gate_required": True,
                **resolution_overrides,
            },
            "existing_capability_refs": [],
            "capability_draft": {
                "tenant_id": context["tenant_id"],
                "organization_id": context["organization_id"],
                "workspace_id": context["workspace_id"],
                "correlation_id": correlation_id,
                "capability_id": "capability_negative_report",
                "version": "0.1.0",
                "name": "Negative report",
                "purpose": "Create an evidence-backed operational report.",
                "owner": context["actor_id"],
                "capability_type": "REPORT",
                "output_state": "DRAFT",
                "lifecycle_state": "DRAFT",
                "scope_refs": ["scope.workspace.factory"],
                "tool_ids": ["report.render"],
                "permission_refs": ["permission.report.create"],
                "prohibited_actions": ["Do not approve or release this proposal."],
                "risk_level": "LOW",
                "evidence_requirements": ["Reference the source records used by the report."],
                "test_requirements": ["Verify the report against an authorized fixture."],
                "human_gate_required": True,
                **draft_overrides,
            },
            "agent_draft": None,
            "missing_dependencies": ["capability_negative_report"],
            "handoff": {
                "target_service": "alos-backend",
                "transport": "typed_internal_api",
                "requested_operations": ["REGISTER_CAPABILITY_DRAFT"],
                "authoritative_state_changed": False,
            },
        }


def principal(**changes: Any) -> Principal:
    values: dict[str, Any] = {
        "actor_id": "actor_negative_requester",
        "tenant_id": "tenant_negative",
        "organization_id": "org_negative",
        "workspace_id": "workspace_negative",
        "permissions": frozenset({"permission.report.create"}),
        "scopes": frozenset({"scope.workspace.factory"}),
        "roles": frozenset({"DIVISION_MEMBER"}),
    }
    values.update(changes)
    return Principal(**values)


def build(genesis: MutableStub) -> tuple[FactoryOrchestrator, CapabilityRegistry]:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    capabilities = CapabilityRegistry(contracts, audit)
    return (
        FactoryOrchestrator(
            contracts=contracts,
            genesis=genesis,
            capabilities=capabilities,
            agents=AgentRegistry(contracts, audit),
        ),
        capabilities,
    )


async def analyze(service: FactoryOrchestrator, correlation_id: str, actor: Principal) -> None:
    await service.analyze(
        {"requirement": "Create an evidence-backed operational report every week."},
        principal=actor,
        correlation_id=correlation_id,
    )


def draft_absent(capabilities: CapabilityRegistry, actor: Principal) -> None:
    with pytest.raises(LookupError):
        capabilities.get(
            tenant_id=actor.tenant_id,
            workspace_id=actor.workspace_id,
            subject_id="capability_negative_report",
            version="0.1.0",
        )


@pytest.mark.asyncio
async def test_ambiguous_requirement_fails_closed_without_state() -> None:
    genesis = MutableStub(understanding={"ambiguity": "NEEDS_CLARIFICATION"})
    service, capabilities = build(genesis)
    actor = principal()

    with pytest.raises(PlatformError) as raised:
        await analyze(service, "corr_negative_ambiguity_001", actor)

    assert raised.value.code == "REQUIREMENT_AMBIGUOUS"
    assert raised.value.status_code == 422
    draft_absent(capabilities, actor)


@pytest.mark.asyncio
async def test_forged_requirement_linkage_is_rejected() -> None:
    genesis = MutableStub(understanding={"requirement_id": "req_forged_by_ai"})
    service, capabilities = build(genesis)
    actor = principal()

    with pytest.raises(PlatformError) as raised:
        await analyze(service, "corr_negative_linkage_001", actor)

    assert raised.value.code == "GENESIS_FACTORY_RESPONSE_INVALID"
    assert raised.value.status_code == 502
    draft_absent(capabilities, actor)


@pytest.mark.asyncio
async def test_ai_cannot_remove_human_gate() -> None:
    genesis = MutableStub(draft={"human_gate_required": False})
    service, capabilities = build(genesis)
    actor = principal()

    with pytest.raises(PlatformError) as raised:
        await analyze(service, "corr_negative_gate_001", actor)

    assert raised.value.code == "GENESIS_HUMAN_GATE_REQUIRED"
    assert raised.value.status_code == 502
    draft_absent(capabilities, actor)


@pytest.mark.asyncio
async def test_ai_cannot_forge_active_lifecycle() -> None:
    genesis = MutableStub(draft={"lifecycle_state": "ACTIVE", "output_state": "APPROVED"})
    service, capabilities = build(genesis)
    actor = principal()

    with pytest.raises(PlatformError) as raised:
        await analyze(service, "corr_negative_lifecycle_001", actor)

    assert raised.value.code == "GENESIS_FACTORY_RESPONSE_INVALID"
    assert raised.value.status_code == 502
    draft_absent(capabilities, actor)


@pytest.mark.asyncio
async def test_ai_cannot_expand_scope() -> None:
    genesis = MutableStub(
        draft={"scope_refs": ["scope.workspace.factory", "scope.tenant.everything"]}
    )
    service, capabilities = build(genesis)
    actor = principal()

    with pytest.raises(PlatformError) as raised:
        await analyze(service, "corr_negative_scope_001", actor)

    assert raised.value.code == "FACTORY_PROPOSAL_NOT_AUTHORIZED"
    assert raised.value.status_code == 403
    assert raised.value.details is not None
    assert raised.value.details["unauthorized_scope_refs"] == ["scope.tenant.everything"]
    draft_absent(capabilities, actor)


@pytest.mark.asyncio
async def test_ai_cannot_self_grant_permission() -> None:
    genesis = MutableStub(
        draft={"permission_refs": ["permission.report.create", "permission.financial.release"]}
    )
    service, capabilities = build(genesis)
    actor = principal()

    with pytest.raises(PlatformError) as raised:
        await analyze(service, "corr_negative_permission_001", actor)

    assert raised.value.code == "FACTORY_PROPOSAL_NOT_AUTHORIZED"
    assert raised.value.status_code == 403
    assert raised.value.details is not None
    assert raised.value.details["missing_permission_refs"] == ["permission.financial.release"]
    draft_absent(capabilities, actor)


@pytest.mark.asyncio
async def test_inactive_principal_and_missing_scope_fail_closed() -> None:
    genesis = MutableStub()
    service, capabilities = build(genesis)

    with pytest.raises(PlatformError) as inactive:
        await analyze(service, "corr_negative_inactive_001", principal(active=False))
    assert inactive.value.code == "INACTIVE_PRINCIPAL"
    assert inactive.value.status_code == 403

    with pytest.raises(PlatformError) as scoped:
        await analyze(service, "corr_negative_scope_002", principal(scopes=frozenset()))
    assert scoped.value.code == "FACTORY_SCOPE_REQUIRED"
    assert scoped.value.status_code == 403
    draft_absent(capabilities, principal())


@pytest.mark.asyncio
async def test_direct_draft_to_active_transition_is_denied() -> None:
    genesis = MutableStub()
    service, capabilities = build(genesis)
    actor = principal()

    await analyze(service, "corr_negative_transition_001", actor)

    entry = capabilities.get(
        tenant_id=actor.tenant_id,
        workspace_id=actor.workspace_id,
        subject_id="capability_negative_report",
        version="0.1.0",
    )
    assert entry.state.value == "DRAFT"
    with pytest.raises(RegistryConflictError):
        await capabilities.activate(
            tenant_id=actor.tenant_id,
            workspace_id=actor.workspace_id,
            subject_id="capability_negative_report",
            version="0.1.0",
            actor_id="actor_negative_requester",
            release_id="release_forged_001",
            correlation_id="corr_negative_transition_002",
        )
    still_draft = capabilities.get(
        tenant_id=actor.tenant_id,
        workspace_id=actor.workspace_id,
        subject_id="capability_negative_report",
        version="0.1.0",
    )
    assert still_draft.state.value == "DRAFT"


@pytest.mark.asyncio
async def test_approval_and_release_lineage_is_recorded_by_backend_governance() -> None:
    genesis = MutableStub()
    service, capabilities = build(genesis)
    actor = principal()

    await analyze(service, "corr_negative_gate_flow_001", actor)

    approved = await capabilities.approve(
        tenant_id=actor.tenant_id,
        workspace_id=actor.workspace_id,
        subject_id="capability_negative_report",
        version="0.1.0",
        actor_id="actor_it_approver",
        decision_id="decision_negative_001",
        authority=DecisionAuthority.IT,
        correlation_id="corr_negative_gate_flow_002",
    )
    assert approved.state.value == "APPROVED"
    assert approved.decision_id == "decision_negative_001"
    activated = await capabilities.activate(
        tenant_id=actor.tenant_id,
        workspace_id=actor.workspace_id,
        subject_id="capability_negative_report",
        version="0.1.0",
        actor_id="actor_release_authority",
        release_id="release_negative_001",
        correlation_id="corr_negative_gate_flow_003",
    )
    assert activated.state.value == "ACTIVE"
    assert activated.release_id == "release_negative_001"
    # Production authority (catalog visibility) exists only after governance.
    snapshot = capabilities.catalog_snapshot(principal=actor)
    assert [item["capability_id"] for item in snapshot] == ["capability_negative_report"]