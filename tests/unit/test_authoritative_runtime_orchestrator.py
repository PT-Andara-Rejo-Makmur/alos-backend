from pathlib import Path
from typing import Any

import pytest

from alos.agents.lifecycle import AgentRunAuthority, AuthoritativeRunStatus
from alos.agents.registry import AgentRegistry
from alos.agents.runtime import AuthoritativeRuntimeOrchestrator
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
from alos.observability.correlation import correlation_id_context
from alos.registry import DecisionAuthority

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


class RecordingGenesisClient:
    def __init__(self) -> None:
        self.invocation: dict[str, Any] | None = None

    async def create_agent_run(
        self, payload: dict[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        self.invocation = payload
        request = payload["run_request"]
        return {
            "run_id": request["run_id"],
            "root_run_id": request["root_run_id"],
            "correlation_id": correlation_id,
            "agent_id": request["agent_id"],
            "agent_version": request["agent_version"],
            "capability_id": request["capability_id"],
            "status": "COMPLETED",
            "output_state": "AI_INFERRED",
            "output": {"summary": "completed", "tool_status": "SUCCESS"},
            "tool_results": [
                {
                    "tool_call_id": "toolcall_runtime_orchestration_001",
                    "run_id": request["run_id"],
                    "tool_id": "diagnostic.echo",
                    "correlation_id": correlation_id,
                    "status": "SUCCESS",
                    "output": {"echo": "hello"},
                    "completed_at": "2026-09-23T10:00:01Z",
                }
            ],
            "usage": {
                "input_tokens": 2,
                "output_tokens": 1,
                "total_tokens": 3,
                "estimated_cost": 0,
            },
            "started_at": "2026-09-23T10:00:00Z",
            "completed_at": "2026-09-23T10:00:02Z",
        }


async def active_agent(contracts: CanonicalContractCatalog, audit: InMemoryAuditRepository):
    registry = AgentRegistry(contracts, audit)
    payload = {
        "tenant_id": "tenant_runtime_orchestration",
        "organization_id": "org_runtime_orchestration",
        "workspace_id": "workspace_runtime_orchestration",
        "agent_id": "agent.runtime.orchestration",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_runtime_orchestration",
        "name": "Runtime orchestration",
        "purpose": "Verify authoritative transport and persistence.",
        "risk_level": "LOW",
        "capability_ids": ["capability.runtime.orchestration"],
        "skill_refs": [],
        "model_policy_ref": "policy.runtime-test",
        "tool_ids": ["diagnostic.echo"],
        "permission_refs": ["tools.diagnostic.execute"],
        "scope_refs": ["scope.diagnostic"],
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }
    entry = await registry.register(
        payload,
        tenant_id=payload["tenant_id"],
        organization_id=payload["organization_id"],
        workspace_id=payload["workspace_id"],
        actor_id=payload["owner_actor_id"],
        correlation_id="corr_runtime_registry_001",
    )
    entry = await registry.approve(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_it_authority",
        decision_id="decision.runtime.orchestration",
        authority=DecisionAuthority.IT,
        correlation_id="corr_runtime_registry_002",
    )
    return await registry.activate(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_release_authority",
        release_id="release.runtime.orchestration",
        correlation_id="corr_runtime_registry_003",
    )


@pytest.mark.asyncio
async def test_orchestrator_derives_authority_and_persists_tool_step() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    genesis = RecordingGenesisClient()
    orchestrator = AuthoritativeRuntimeOrchestrator(
        authority=authority,
        genesis=genesis,  # type: ignore[arg-type]
    )
    principal = Principal(
        actor_id="actor_runtime_orchestration",
        tenant_id="tenant_runtime_orchestration",
        organization_id="org_runtime_orchestration",
        workspace_id="workspace_runtime_orchestration",
        permissions=frozenset({"tools.diagnostic.execute"}),
        scopes=frozenset({"scope.diagnostic"}),
    )

    correlation_token = correlation_id_context.set("corr_runtime_orchestration_001")
    try:
        completed = await orchestrator.execute(
            {
                "capability_id": "capability.runtime.orchestration",
                "input": {"message": "hello"},
                "requested_tool_ids": ["diagnostic.echo"],
                "scope_refs": ["scope.diagnostic"],
                "execution_budget": {
                    "max_tokens": 100,
                    "max_steps": 3,
                    "max_tool_calls": 1,
                },
                "execution_mode": "TEST",
            },
            principal=principal,
            agent=await active_agent(contracts, audit),
            test_mode_allowed=True,
        )
    finally:
        correlation_id_context.reset(correlation_token)

    assert completed.status is AuthoritativeRunStatus.COMPLETED
    assert completed.total_tokens == 3
    assert genesis.invocation is not None
    assert genesis.invocation["runtime_authorization"]["allowed_tool_ids"] == [
        "diagnostic.echo"
    ]
    steps = await authority.list_steps(completed.run_id)
    assert len(steps) == 1
    assert steps[0].tool_id == "diagnostic.echo"
    assert steps[0].output_metadata["status"] == "SUCCESS"
