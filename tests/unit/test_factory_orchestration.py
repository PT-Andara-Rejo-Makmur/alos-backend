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
from alos.registry import DecisionAuthority, RegistryState
from alos.security.errors import PlatformError

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"


class StubGenesis:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.request: dict[str, Any] | None = None
        self.correlation_id: str | None = None

    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        self.request = dict(payload)
        self.correlation_id = correlation_id
        requirement = payload["requirement"]
        assert isinstance(requirement, Mapping)
        context = requirement["execution_context"]
        assert isinstance(context, Mapping)
        catalog = payload["capability_catalog"]
        assert isinstance(catalog, list)
        if self.decision == "REUSE":
            reference = catalog[0]
            return factory_result(
                context=context,
                decision="REUSE",
                existing=[reference],
                capability_draft=None,
            )
        return factory_result(
            context=context,
            decision="CREATE",
            existing=[],
            capability_draft=capability_draft(context),
        )


def principal() -> Principal:
    return Principal(
        actor_id="actor_factory_requester",
        tenant_id="tenant_factory",
        organization_id="org_factory",
        workspace_id="workspace_factory",
        permissions=frozenset(
            {"permission.report.create", "permission.admin.all", "permission.secret.read"}
        ),
        scopes=frozenset({"scope.workspace.factory"}),
        roles=frozenset({"DIVISION_MEMBER"}),
    )


def capability_draft(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": context["tenant_id"],
        "organization_id": context["organization_id"],
        "workspace_id": context["workspace_id"],
        "correlation_id": context["correlation_id"],
        "capability_id": "capability_factory_report",
        "version": "0.1.0",
        "name": "Factory report",
        "purpose": "Create an evidence-backed operational report for authorized reviewers.",
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
        "constraints": ["Backend approval is required before activation."],
    }


def factory_result(
    *,
    context: Mapping[str, Any],
    decision: str,
    existing: list[dict[str, Any]],
    capability_draft: dict[str, Any] | None,
) -> dict[str, Any]:
    create = decision == "CREATE"
    return {
        "correlation_id": context["correlation_id"],
        "resolution": {
            "understanding": {
                "normalized_intent": "Create an evidence-backed operational report.",
                "domains": ["operations"],
                "candidate_capability_ids": (
                    ["capability_existing_report"] if not create else []
                ),
                "recommended_type": "REPORT",
                "risk_level": "LOW",
                "requires_agent": False,
                "rationale": ["A report capability is sufficient."],
            },
            "decision": decision,
            "reason": "Reuse an authorized capability." if not create else "No match exists.",
            "purpose": "Create an evidence-backed operational report.",
            "scope_refs": ["scope.workspace.factory"],
            "resolved": existing,
            "missing_capability_ids": ([] if not create else ["capability_factory_report"]),
            "required_tool_ids": ["report.render"],
            "required_permission_refs": ["permission.report.create"],
            "evidence_requirements": ["Reference the source records used by the report."],
            "test_requirements": ["Verify the report against an authorized fixture."],
            "activation_readiness": "READY_FOR_REUSE" if not create else "READY_FOR_DRAFT",
        },
        "existing_capability_refs": existing,
        "capability_draft": capability_draft,
        "agent_draft": None,
        "missing_dependencies": ([] if not create else ["capability_factory_report"]),
        "handoff": {
            "target_service": "alos-backend",
            "transport": "typed_internal_api",
            "requested_operations": ([] if not create else ["REGISTER_CAPABILITY_DRAFT"]),
            "authoritative_state_changed": False,
        },
    }


def orchestrator(
    genesis: StubGenesis,
) -> tuple[FactoryOrchestrator, CapabilityRegistry, InMemoryAuditRepository]:
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
        audit,
    )


@pytest.mark.asyncio
async def test_create_registers_canonical_draft_with_backend_authority() -> None:
    genesis = StubGenesis("CREATE")
    service, capabilities, _audit = orchestrator(genesis)

    response = await service.analyze(
        {"requirement": "Create an evidence-backed operational report every week."},
        principal=principal(),
        correlation_id="corr_factory_create_001",
    )

    assert response["decision"] == "CREATE"
    assert response["registry_result"]["state"] == "DRAFT"
    assert response["registry_result"]["registered_refs"] == [
        {
            "subject_type": "CAPABILITY",
            "identifier": "capability_factory_report",
            "version": "0.1.0",
            "state": "DRAFT",
        }
    ]
    entry = capabilities.get(
        tenant_id="tenant_factory",
        workspace_id="workspace_factory",
        subject_id="capability_factory_report",
        version="0.1.0",
    )
    assert entry.state is RegistryState.DRAFT
    assert entry.payload["permission_refs"] == ["permission.report.create"]
    assert "permission.admin.all" not in entry.payload["permission_refs"]
    assert genesis.request is not None
    context = genesis.request["requirement"]["execution_context"]
    assert context["permission_refs"] == [
        "permission.admin.all",
        "permission.report.create",
        "permission.secret.read",
    ]
    assert context["correlation_id"] == response["correlation_id"]


@pytest.mark.asyncio
async def test_reuse_returns_existing_reference_without_registry_write() -> None:
    genesis = StubGenesis("REUSE")
    service, capabilities, audit = orchestrator(genesis)
    actor = principal()
    draft = await capabilities.register(
        {
            "tenant_id": actor.tenant_id,
            "organization_id": actor.organization_id,
            "workspace_id": actor.workspace_id,
            "capability_id": "capability_existing_report",
            "version": "1.0.0",
            "name": "Existing report",
            "purpose": "Create an evidence-backed operational report.",
            "owner": actor.actor_id,
            "capability_type": "REPORT",
            "lifecycle_state": "ACTIVE",
            "risk_level": "LOW",
            "availability": "AVAILABLE",
            "configuration_status": "CONFIGURED",
            "scope_refs": ["scope.workspace.factory"],
            "permission_refs": ["permission.report.create"],
            "backing_tool_ids": ["report.render"],
        },
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        actor_id=actor.actor_id,
        correlation_id="corr_factory_seed_001",
    )
    approved = await capabilities.approve(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        subject_id=draft.subject_id,
        version=draft.version,
        actor_id="actor_it_approver",
        decision_id="decision_factory_001",
        authority=DecisionAuthority.IT,
        correlation_id="corr_factory_seed_002",
    )
    await capabilities.activate(
        tenant_id=approved.tenant_id,
        workspace_id=approved.workspace_id,
        subject_id=approved.subject_id,
        version=approved.version,
        actor_id="actor_release",
        release_id="release_factory_001",
        correlation_id="corr_factory_seed_003",
    )
    event_count = len(audit.list_events(tenant_id=actor.tenant_id))

    response = await service.analyze(
        {"requirement": "Reuse the existing evidence-backed operational report capability."},
        principal=actor,
        correlation_id="corr_factory_reuse_001",
    )

    assert response["decision"] == "REUSE"
    assert response["capability_draft"] is None
    assert response["agent_draft"] is None
    assert response["registry_result"] is None
    assert response["existing_capability_refs"][0]["capability_id"] == draft.subject_id
    assert len(audit.list_events(tenant_id=actor.tenant_id)) == event_count


@pytest.mark.asyncio
async def test_create_rejects_permissions_outside_backend_actor_authority() -> None:
    genesis = StubGenesis("CREATE")
    service, capabilities, _audit = orchestrator(genesis)
    actor = principal()
    restricted_actor = Principal(
        actor_id=actor.actor_id,
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        permissions=frozenset({"permission.document.read"}),
        scopes=actor.scopes,
        roles=actor.roles,
    )

    with pytest.raises(PlatformError) as raised:
        await service.analyze(
            {"requirement": "Create an evidence-backed operational report every week."},
            principal=restricted_actor,
            correlation_id="corr_factory_denied_001",
        )

    assert raised.value.code == "FACTORY_PROPOSAL_NOT_AUTHORIZED"
    assert raised.value.status_code == 403
    with pytest.raises(LookupError):
        capabilities.get(
            tenant_id=actor.tenant_id,
            workspace_id=actor.workspace_id,
            subject_id="capability_factory_report",
            version="0.1.0",
        )
