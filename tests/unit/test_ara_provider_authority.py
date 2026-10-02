"""Provider configuration never manufactures released ARA execution authority."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alos.agents.registry import AgentRegistry
from alos.ara.orchestration import AraOrchestrator
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
from alos.registry import RegistryEntry, RegistryState

ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "released",
        "draft",
        "no_release",
        "foreign_tenant",
        "foreign_org",
        "foreign_workspace",
        "foreign_scope",
        "unknown_tool",
        "test_policy",
        "unbounded",
        "multiple_versions",
    ],
)
async def test_production_ara_requires_existing_authorized_release(case: str) -> None:
    principal = Principal(
        actor_id="actor_ara",
        tenant_id="tenant_ara",
        organization_id="org_ara",
        workspace_id="workspace_ara",
        scopes=frozenset({"scope.business"}),
        permissions=frozenset({"sales.read"}),
    )
    agents = AgentRegistry(CanonicalContractCatalog(ROOT), InMemoryAuditRepository())
    definition = {
        "agent_id": "ara.workspace-assistant",
        "model_policy_ref": "ara.production",
        "owner_actor_id": principal.actor_id,
        "risk_level": "LOW",
        "scope_refs": ["foreign"] if case == "foreign_scope" else ["scope.business"],
        "capability_ids": ["business.question_answering"],
        "tool_ids": ["admin.approve"] if case == "unknown_tool" else ["sales.lead.list"],
        "execution_budget": {
            "max_tokens": 1000,
            "max_steps": 3,
            "max_tool_calls": 1,
            "timeout_seconds": 10,
        },
    }
    if case == "test_policy":
        definition["model_policy_ref"] = "ara.deterministic"
    if case == "unbounded":
        definition["execution_budget"].pop("max_tokens")
    for version in ["1.0.0", "1.1.0"] if case == "multiple_versions" else ["1.0.0"]:
        entry = RegistryEntry(
            subject_type="agent",
            subject_id="ara.workspace-assistant",
            version=version,
            tenant_id="foreign" if case == "foreign_tenant" else principal.tenant_id,
            organization_id="foreign" if case == "foreign_org" else principal.organization_id,
            workspace_id="foreign" if case == "foreign_workspace" else principal.workspace_id,
            payload=definition,
            digest=hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest(),
            state=RegistryState.DRAFT if case == "draft" else RegistryState.ACTIVE,
            created_by=principal.actor_id,
            correlation_id="corr_release",
            created_at=datetime.now(UTC),
            release_id=None if case == "no_release" else "release.fixture",
        )
        await agents.publish_committed(entry)
    service = AraOrchestrator(
        repository=None,
        contracts=None,
        authority=None,
        genesis=None,
        registry=None,
        audit=None,
        test_enabled=False,
        agents=agents,
    )
    assert service.runtime_mode == "NORMAL"
    if case == "released":
        assert service.production_agent(principal).release_id == "release.fixture"
    else:
        with pytest.raises(ValueError):
            service.production_agent(principal)
    assert len(
        agents.list_entries(
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
        )
    ) <= (2 if case == "multiple_versions" else 1)
