from pathlib import Path

import pytest

from alos.agents.lifecycle import AgentRunAuthority, RunAuthorityError
from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.registry import DecisionAuthority

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def agent_definition() -> dict[str, object]:
    return {
        "tenant_id": "tenant_runtime_001",
        "organization_id": "org_runtime_001",
        "workspace_id": "workspace_runtime_001",
        "correlation_id": "corr_registry_runtime_001",
        "agent_id": "agent_runtime_diagnostic",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_runtime_001",
        "name": "Runtime diagnostic",
        "purpose": "Verify governed runtime split.",
        "risk_level": "LOW",
        "capability_ids": ["capability_runtime_diagnostic"],
        "skill_refs": [],
        "model_policy_ref": "policy.runtime-test",
        "tool_ids": ["diagnostic.echo"],
        "permission_refs": ["tools.diagnostic.execute"],
        "scope_refs": ["scope.diagnostic"],
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }


def run_request() -> dict[str, object]:
    return {
        "run_id": "run_runtime_split_001",
        "root_run_id": "run_runtime_split_001",
        "agent_id": "agent_runtime_diagnostic",
        "agent_version": "1.0.0",
        "capability_id": "capability_runtime_diagnostic",
        "execution_context": {
            "tenant_id": "tenant_runtime_001",
            "organization_id": "org_runtime_001",
            "workspace_id": "workspace_runtime_001",
            "actor_id": "actor_runtime_001",
            "authority_context": {
                "role": "diagnostic_runner",
                "authority_level": "SYSTEM",
            },
            "permission_refs": ["tools.diagnostic.execute"],
            "scope_refs": ["scope.diagnostic"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_runtime_split_001",
            "execution_budget": {
                "max_tokens": 200,
                "max_steps": 2,
                "max_tool_calls": 1,
                "timeout_seconds": 5,
            },
        },
        "input": {"message": "runtime split"},
        "requested_tool_ids": ["diagnostic.echo"],
    }


def run_result() -> dict[str, object]:
    return {
        "run_id": "run_runtime_split_001",
        "root_run_id": "run_runtime_split_001",
        "correlation_id": "corr_runtime_split_001",
        "agent_id": "agent_runtime_diagnostic",
        "agent_version": "1.0.0",
        "capability_id": "capability_runtime_diagnostic",
        "status": "COMPLETED",
        "output_state": "AI_INFERRED",
        "output": {"summary": "complete"},
        "tool_results": [],
        "evidence_refs": [],
        "started_at": "2026-09-17T10:00:00Z",
        "completed_at": "2026-09-17T10:00:01Z",
    }


async def active_agent(
    contracts: CanonicalContractCatalog,
    audit: InMemoryAuditRepository,
):
    registry = AgentRegistry(contracts, audit)
    entry = await registry.register(
        agent_definition(),
        tenant_id="tenant_runtime_001",
        organization_id="org_runtime_001",
        workspace_id="workspace_runtime_001",
        actor_id="actor_runtime_001",
        correlation_id="corr_registry_runtime_001",
    )
    entry = await registry.approve(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_it_001",
        decision_id="decision_runtime_001",
        authority=DecisionAuthority.IT,
        correlation_id="corr_registry_runtime_002",
    )
    return await registry.activate(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_release_001",
        release_id="release_runtime_001",
        correlation_id="corr_registry_runtime_003",
    )


@pytest.mark.asyncio
async def test_backend_owns_authoritative_run_lifecycle_and_audit() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    agent = await active_agent(contracts, audit)
    authority = AgentRunAuthority(contracts=contracts, audit=audit)

    started = await authority.begin(run_request(), agent=agent)
    authorization = await authority.runtime_authorization(started.run_id)
    completed = await authority.complete(run_result())

    assert started.status.value == "RUNNING"
    assert authorization == {
        "run_id": "run_runtime_split_001",
        "registry_digest": agent.digest,
        "lifecycle_state": "ACTIVE",
        "allowed_tool_ids": ["diagnostic.echo"],
        "authorized_skill_refs": [],
    }
    assert completed.status.value == "COMPLETED"
    run_events = [event.event_type for event in audit.list_events(tenant_id=agent.tenant_id)]
    assert "run.started" in run_events
    assert "run.completed" in run_events


@pytest.mark.asyncio
async def test_backend_denies_run_when_permissions_are_missing() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    agent = await active_agent(contracts, audit)
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    payload = run_request()
    payload["execution_context"]["permission_refs"] = []  # type: ignore[index]

    with pytest.raises(RunAuthorityError, match="lacks Agent permissions"):
        await authority.begin(payload, agent=agent)


@pytest.mark.asyncio
async def test_backend_denies_run_when_scope_is_missing() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    agent = await active_agent(contracts, audit)
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    payload = run_request()
    payload["execution_context"]["scope_refs"] = ["scope.other"]  # type: ignore[index]

    with pytest.raises(RunAuthorityError, match="lacks Agent scope"):
        await authority.begin(payload, agent=agent)


@pytest.mark.asyncio
async def test_backend_rejects_mismatched_runtime_result() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    agent = await active_agent(contracts, audit)
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    await authority.begin(run_request(), agent=agent)
    result = run_result()
    result["correlation_id"] = "corr_wrong_001"

    with pytest.raises(RunAuthorityError, match="does not match"):
        await authority.complete(result)


@pytest.mark.asyncio
async def test_backend_rejects_tool_intent_outside_agent_definition() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    agent = await active_agent(contracts, audit)
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    payload = run_request()
    payload["requested_tool_ids"] = ["diagnostic.unrestricted"]

    with pytest.raises(RunAuthorityError, match="outside Agent definition"):
        await authority.begin(payload, agent=agent)
