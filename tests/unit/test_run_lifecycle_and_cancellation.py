from pathlib import Path

import pytest

from alos.agents.lifecycle import AgentRunAuthority, AuthoritativeRunStatus, AuthoritativeStepStatus
from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.registry import DecisionAuthority

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def agent_definition() -> dict[str, object]:
    return {
        "tenant_id": "tenant_runtime_002",
        "organization_id": "org_runtime_002",
        "workspace_id": "workspace_runtime_002",
        "correlation_id": "corr_registry_runtime_010",
        "agent_id": "agent_runtime_lifecycle",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_runtime_002",
        "name": "Lifecycle test agent",
        "purpose": "Verify run persistence and cancellation.",
        "risk_level": "LOW",
        "capability_ids": ["capability_runtime_lifecycle"],
        "skill_refs": [],
        "model_policy_ref": "policy.runtime-test",
        "tool_ids": ["diagnostic.echo"],
        "permission_refs": ["tools.diagnostic.execute"],
        "scope_refs": ["scope.diagnostic"],
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }


def run_request() -> dict[str, object]:
    return {
        "run_id": "run_runtime_lifecycle_001",
        "root_run_id": "run_runtime_lifecycle_001",
        "agent_id": "agent_runtime_lifecycle",
        "agent_version": "1.0.0",
        "capability_id": "capability_runtime_lifecycle",
        "execution_context": {
            "tenant_id": "tenant_runtime_002",
            "organization_id": "org_runtime_002",
            "workspace_id": "workspace_runtime_002",
            "actor_id": "actor_runtime_002",
            "authority_context": {"role": "diagnostic_runner", "authority_level": "SYSTEM"},
            "permission_refs": ["tools.diagnostic.execute"],
            "scope_refs": ["scope.diagnostic"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_runtime_lifecycle_001",
            "execution_budget": {
                "max_tokens": 200,
                "max_steps": 2,
                "max_tool_calls": 1,
                "timeout_seconds": 5,
            },
        },
        "input": {"message": "lifecycle test"},
        "requested_tool_ids": ["diagnostic.echo"],
    }


async def active_agent(contracts: CanonicalContractCatalog, audit: InMemoryAuditRepository):
    registry = AgentRegistry(contracts, audit)
    entry = await registry.register(
        agent_definition(),
        tenant_id="tenant_runtime_002",
        organization_id="org_runtime_002",
        workspace_id="workspace_runtime_002",
        actor_id="actor_runtime_002",
        correlation_id="corr_registry_runtime_010",
    )
    entry = await registry.approve(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_it_002",
        decision_id="decision_runtime_010",
        authority=DecisionAuthority.IT,
        correlation_id="corr_registry_runtime_011",
    )
    return await registry.activate(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_release_002",
        release_id="release_runtime_010",
        correlation_id="corr_registry_runtime_012",
    )


@pytest.mark.asyncio
async def test_authoritative_run_can_record_steps_and_cancel() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    agent = await active_agent(contracts, audit)
    authority = AgentRunAuthority(contracts=contracts, audit=audit)

    started = await authority.begin(run_request(), agent=agent)
    step = await authority.begin_step(
        started.run_id,
        step_type="tool",
        tool_id="diagnostic.echo",
        input_metadata={"message": "hello"},
        correlation_id="corr_step_lifecycle_001",
    )

    assert step.run_id == started.run_id
    assert step.status is AuthoritativeStepStatus.RUNNING
    assert step.sequence == 1

    cancelled = await authority.cancel(
        started.run_id,
        actor_id="actor_runtime_002",
        reason="Operator requested cancellation.",
        correlation_id="corr_cancel_lifecycle_001",
    )

    assert cancelled.status is AuthoritativeRunStatus.CANCELLED
    cancellation_state = getattr(cancelled, "cancellation_state", None)
    assert cancellation_state in {"REQUESTED", "CANCELLED"}

    updated = await authority.update_step_status(
        step.step_id,
        status=AuthoritativeStepStatus.CANCELLED,
        error_code="RUN_CANCELLED",
        error_message="run was cancelled before completion",
    )
    assert updated.status is AuthoritativeStepStatus.CANCELLED
    assert updated.error_code == "RUN_CANCELLED"
