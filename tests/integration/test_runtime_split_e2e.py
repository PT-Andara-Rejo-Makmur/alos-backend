from pathlib import Path

import httpx
import pytest

from alos.agents.lifecycle import AgentRunAuthority
from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.config import Settings
from alos.contracts import CanonicalContractCatalog
from alos.main import create_app
from alos.registry import DecisionAuthority

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def execution_context() -> dict[str, object]:
    return {
        "tenant_id": "tenant_diagnostic_001",
        "organization_id": "org_diagnostic_001",
        "workspace_id": "workspace_diagnostic_001",
        "actor_id": "actor_diagnostic_001",
        "authority_context": {
            "role": "diagnostic_runner",
            "authority_level": "SYSTEM",
        },
        "permission_refs": ["tools.diagnostic.execute"],
        "scope_refs": ["scope.diagnostic"],
        "data_classification": "INTERNAL",
        "correlation_id": "corr_runtime_e2e_001",
        "execution_budget": {
            "max_tokens": 200,
            "max_steps": 2,
            "max_tool_calls": 1,
            "timeout_seconds": 5,
        },
    }


def agent_definition() -> dict[str, object]:
    return {
        "tenant_id": "tenant_diagnostic_001",
        "organization_id": "org_diagnostic_001",
        "workspace_id": "workspace_diagnostic_001",
        "correlation_id": "corr_registry_e2e_001",
        "agent_id": "agent_runtime_e2e",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_diagnostic_001",
        "name": "Runtime E2E",
        "purpose": "Verify the multi-repository runtime boundary.",
        "risk_level": "LOW",
        "capability_ids": ["capability_runtime_e2e"],
        "skill_refs": [],
        "model_policy_ref": "policy.runtime-test",
        "tool_ids": ["diagnostic.echo"],
        "permission_refs": ["tools.diagnostic.execute"],
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }


def agent_run_request() -> dict[str, object]:
    return {
        "run_id": "run_runtime_e2e_001",
        "root_run_id": "run_runtime_e2e_001",
        "agent_id": "agent_runtime_e2e",
        "agent_version": "1.0.0",
        "capability_id": "capability_runtime_e2e",
        "execution_context": execution_context(),
        "input": {"message": "multi-repo runtime"},
        "requested_tool_ids": ["diagnostic.echo"],
        "execution_mode": "TEST",
    }


@pytest.mark.asyncio
async def test_genesis_tool_request_returns_through_authoritative_runtime_boundary() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    run_audit = InMemoryAuditRepository()
    registry = AgentRegistry(contracts, run_audit)
    agent = await registry.register(
        agent_definition(),
        tenant_id="tenant_diagnostic_001",
        organization_id="org_diagnostic_001",
        workspace_id="workspace_diagnostic_001",
        actor_id="actor_diagnostic_001",
        correlation_id="corr_registry_e2e_001",
    )
    agent = await registry.approve(
        tenant_id=agent.tenant_id,
        workspace_id=agent.workspace_id,
        subject_id=agent.subject_id,
        version=agent.version,
        actor_id="actor_it_001",
        decision_id="decision_runtime_e2e_001",
        authority=DecisionAuthority.IT,
        correlation_id="corr_registry_e2e_002",
    )
    agent = await registry.activate(
        tenant_id=agent.tenant_id,
        workspace_id=agent.workspace_id,
        subject_id=agent.subject_id,
        version=agent.version,
        actor_id="actor_release_001",
        release_id="release_runtime_e2e_001",
        correlation_id="corr_registry_e2e_003",
    )
    run_authority = AgentRunAuthority(contracts=contracts, audit=run_audit)
    started = await run_authority.begin(agent_run_request(), agent=agent)

    app = create_app(
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
            GENESIS_BASE_URL="http://genesis.test",
            GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
            ENABLE_TEST_TOOLS=True,
        )
    )
    tool_request = {
        "tool_call_id": "toolcall_runtime_e2e_001",
        "run_id": started.run_id,
        "tool_id": "diagnostic.echo",
        "execution_context": execution_context(),
        "arguments": {"message": "multi-repo runtime"},
        "requested_at": "2026-09-17T10:00:00Z",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://backend.test",
    ) as client:
        response = await client.post(
            "/internal/v1/tool-requests",
            headers={
                "Authorization": "Bearer test-only-token",
                "X-Correlation-ID": "corr_runtime_e2e_001",
            },
            json=tool_request,
        )

    assert response.status_code == 200
    tool_result = response.json()
    assert tool_result["status"] == "SUCCESS"
    assert tool_result["correlation_id"] == "corr_runtime_e2e_001"
    completed = await run_authority.complete(
        {
            "run_id": started.run_id,
            "root_run_id": started.root_run_id,
            "correlation_id": started.correlation_id,
            "agent_id": started.agent_id,
            "agent_version": started.agent_version,
            "capability_id": started.capability_id,
            "status": "COMPLETED",
            "output_state": "AI_INFERRED",
            "output": {"summary": "governed runtime completed"},
            "tool_results": [tool_result],
            "evidence_refs": [],
            "started_at": "2026-09-17T10:00:00Z",
            "completed_at": "2026-09-17T10:00:02Z",
        }
    )

    assert completed.status.value == "COMPLETED"
    assert [record.outcome for record in app.state.tool_audit_sink.records] == [
        "REQUESTED",
        "SUCCESS",
    ]
    run_events = [
        event.event_type for event in run_audit.list_events(tenant_id="tenant_diagnostic_001")
    ]
    assert "run.started" in run_events
    assert "run.completed" in run_events
