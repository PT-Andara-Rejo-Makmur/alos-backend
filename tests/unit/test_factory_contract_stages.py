"""Positive stage-contract tests for the frozen MVP2 Factory flow (M2-H01-BE-02).

Requirement -> RequirementUnderstanding -> CapabilityDecision -> CapabilityDraft
must all be produced as typed, governed Backend output without database detail.
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
from alos.registry import RegistryAuthorizationError, RegistryState

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"

PUBLIC_RESPONSE_KEYS = {
    "correlation_id",
    "decision",
    "reason",
    "existing_capability_refs",
    "capability_draft",
    "agent_draft",
    "registry_result",
}

FORBIDDEN_INTERNAL_KEYS = {"sql", "query", "table", "columns", "row", "connection"}


class CreateStub:
    """Deterministic GENESIS proposal used to exercise the Backend contract stages."""

    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        self.request = dict(payload)
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
                "scope_refs": ["scope.workspace.factory"],
                "resolved": [],
                "missing_capability_ids": ["capability_stage_report"],
                "required_tool_ids": ["report.render"],
                "required_permission_refs": ["permission.report.create"],
                "evidence_requirements": ["Reference the source records used by the report."],
                "test_requirements": ["Verify the report against an authorized fixture."],
                "activation_readiness": "READY_FOR_DRAFT",
                "human_gate_required": True,
            },
            "existing_capability_refs": [],
            "capability_draft": {
                "tenant_id": context["tenant_id"],
                "organization_id": context["organization_id"],
                "workspace_id": context["workspace_id"],
                "correlation_id": correlation_id,
                "capability_id": "capability_stage_report",
                "version": "0.1.0",
                "name": "Stage report",
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
            },
            "agent_draft": None,
            "missing_dependencies": ["capability_stage_report"],
            "handoff": {
                "target_service": "alos-backend",
                "transport": "typed_internal_api",
                "requested_operations": ["REGISTER_CAPABILITY_DRAFT"],
                "authoritative_state_changed": False,
            },
        }


def principal() -> Principal:
    return Principal(
        actor_id="actor_stage_requester",
        tenant_id="tenant_stage",
        organization_id="org_stage",
        workspace_id="workspace_stage",
        permissions=frozenset({"permission.report.create"}),
        scopes=frozenset({"scope.workspace.factory"}),
        roles=frozenset({"DIVISION_MEMBER"}),
    )


def build(genesis: CreateStub) -> tuple[FactoryOrchestrator, CapabilityRegistry]:
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


async def analyze(service: FactoryOrchestrator, correlation_id: str) -> dict[str, Any]:
    return await service.analyze(
        {"requirement": "Create an evidence-backed operational report every week."},
        principal=principal(),
        correlation_id=correlation_id,
    )


@pytest.mark.asyncio
async def test_requirement_stage_is_typed_and_backend_derived() -> None:
    genesis = CreateStub()
    service, _capabilities = build(genesis)

    response = await analyze(service, "corr_stage_001")

    assert genesis.request is not None
    requirement = genesis.request["requirement"]
    assert requirement["requirement_id"] == "req_corr_stage_001"
    assert requirement["statement"] == "Create an evidence-backed operational report every week."
    assert set(requirement["execution_context"]) >= {
        "tenant_id",
        "organization_id",
        "workspace_id",
        "actor_id",
        "scope_refs",
        "permission_refs",
        "correlation_id",
    }
    assert response["correlation_id"] == "corr_stage_001"


@pytest.mark.asyncio
async def test_understanding_decision_and_draft_are_typed_stages() -> None:
    genesis = CreateStub()
    service, _capabilities = build(genesis)

    response = await analyze(service, "corr_stage_002")

    assert set(response) == PUBLIC_RESPONSE_KEYS
    assert not FORBIDDEN_INTERNAL_KEYS & {key.lower() for key in response}
    assert response["decision"] == "CREATE"
    assert response["reason"]

    draft = response["capability_draft"]
    assert draft["capability_id"] == "capability_stage_report"
    assert draft["version"] == "0.1.0"
    assert draft["lifecycle_state"] == "DRAFT"
    assert draft["output_state"] == "DRAFT"
    assert draft["human_gate_required"] is True
    assert draft["owner"] == "actor_stage_requester"
    assert draft["scope_refs"] == ["scope.workspace.factory"]
    assert draft["tool_ids"] == ["report.render"]
    assert draft["permission_refs"] == ["permission.report.create"]
    assert draft["prohibited_actions"]
    assert draft["evidence_requirements"]
    assert draft["test_requirements"]

    registry_result = response["registry_result"]
    assert registry_result["state"] == "DRAFT"
    assert registry_result["registered_refs"][0]["state"] == "DRAFT"


@pytest.mark.asyncio
async def test_registered_draft_has_no_production_authority() -> None:
    genesis = CreateStub()
    service, capabilities = build(genesis)
    actor = principal()

    await analyze(service, "corr_stage_003")

    entry = capabilities.get(
        tenant_id=actor.tenant_id,
        workspace_id=actor.workspace_id,
        subject_id="capability_stage_report",
        version="0.1.0",
    )
    assert entry.state is RegistryState.DRAFT
    # A DRAFT is not an ACTIVE production capability: the catalog stays empty.
    assert capabilities.catalog_snapshot(principal=actor) == ()
    with pytest.raises(RegistryAuthorizationError):
        capabilities.get_authorized(
            principal=actor,
            subject_id="capability_stage_report",
            version="0.1.0",
        )


@pytest.mark.asyncio
async def test_backend_records_human_gate_in_authoritative_definition() -> None:
    genesis = CreateStub()
    service, capabilities = build(genesis)
    actor = principal()

    await analyze(service, "corr_stage_004")

    entry = capabilities.get(
        tenant_id=actor.tenant_id,
        workspace_id=actor.workspace_id,
        subject_id="capability_stage_report",
        version="0.1.0",
    )
    metadata = entry.payload["metadata"]
    assert metadata["human_gate_required"] is True
    assert metadata["proposal_lifecycle_state"] == "DRAFT"