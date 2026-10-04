from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from alos.agents.lifecycle import AgentRunAuthority, AuthoritativeRunStatus
from alos.agents.registry import AgentRegistry
from alos.agents.runtime import AuthoritativeRuntimeOrchestrator
from alos.api.public.routes import bootstrap_deterministic_integration, execute_agent_run
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
from alos.integrations.genesis.client import GenesisClientError
from alos.observability.correlation import correlation_id_context
from alos.registry import DecisionAuthority
from alos.security.errors import PlatformError

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


class RecordingGenesisClient:
    def __init__(self) -> None:
        self.invocation: dict[str, Any] | None = None
        self.timeout_seconds: float | None = None

    async def create_agent_run(
        self, payload: dict[str, Any], *, correlation_id: str, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        self.invocation = payload
        self.timeout_seconds = timeout_seconds
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


async def active_agent(
    contracts: CanonicalContractCatalog,
    audit: InMemoryAuditRepository,
    budget_deadline: int | None = None,
):
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
        "execution_budget": {
            "max_tokens": 100,
            "max_steps": 3,
            "max_tool_calls": 1,
        },
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }
    if budget_deadline is not None:
        payload["execution_budget"]["timeout_seconds"] = budget_deadline
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
                    "max_tokens": 1_000_000,
                    "max_steps": 1_000,
                    "max_tool_calls": 1_000,
                },
                "data_classification": "PUBLIC",
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
    assert genesis.timeout_seconds == 35
    assert genesis.invocation is not None
    assert genesis.invocation["runtime_authorization"]["allowed_tool_ids"] == ["diagnostic.echo"]
    assert genesis.invocation["run_request"]["execution_context"]["execution_budget"] == {
        "max_tokens": 100,
        "max_steps": 3,
        "max_tool_calls": 1,
        "timeout_seconds": 30,
    }
    assert (
        genesis.invocation["run_request"]["execution_context"]["data_classification"] == "INTERNAL"
    )
    steps = await authority.list_steps(completed.run_id)
    assert len(steps) == 1
    assert steps[0].tool_id == "diagnostic.echo"
    assert steps[0].output_metadata["status"] == "SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_deadline", [None, 7])
@pytest.mark.parametrize(
    "code", ["GENESIS_TIMEOUT", "GENESIS_UNAVAILABLE", "GENESIS_INVALID_RESPONSE"]
)
async def test_transport_failure_terminates_authoritative_run(
    code: str, budget_deadline: int | None
) -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    genesis = RecordingGenesisClient()
    genesis.create_agent_run = AsyncMock(
        side_effect=GenesisClientError(
            code=code, message="transport failed", correlation_id="corr_failure", retryable=True
        )
    )
    orchestrator = AuthoritativeRuntimeOrchestrator(authority=authority, genesis=genesis)
    principal = Principal(
        actor_id="actor_runtime_orchestration",
        tenant_id="tenant_runtime_orchestration",
        organization_id="org_runtime_orchestration",
        workspace_id="workspace_runtime_orchestration",
        permissions=frozenset({"tools.diagnostic.execute"}),
        scopes=frozenset({"scope.diagnostic"}),
    )
    with pytest.raises(GenesisClientError):
        await orchestrator.execute(
            {
                "capability_id": "capability.runtime.orchestration",
                "execution_mode": "TEST",
                "requested_tool_ids": ["diagnostic.echo"],
                "input": {"message": "hello"},
                "execution_budget": {"timeout_seconds": 999999},
            },
            principal=principal,
            agent=await active_agent(contracts, audit, budget_deadline),
            test_mode_allowed=True,
        )
    assert (
        genesis.create_agent_run.call_args.kwargs["timeout_seconds"] == (budget_deadline or 30) + 5
    )
    (record,) = await authority.list_runs()
    assert record.status is (
        AuthoritativeRunStatus.TIMED_OUT
        if code == "GENESIS_TIMEOUT"
        else AuthoritativeRunStatus.FAILED
    )
    assert record.result["error"]["code"] == code


@pytest.mark.asyncio
async def test_public_run_route_rejects_client_selected_authority_fields() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    principal = Principal(
        actor_id="actor_runtime_orchestration",
        tenant_id="tenant_runtime_orchestration",
        organization_id="org_runtime_orchestration",
        workspace_id="workspace_runtime_orchestration",
        permissions=frozenset({"tools.diagnostic.execute"}),
        scopes=frozenset({"scope.diagnostic"}),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_registry=object())))
    payload = {
        "agent_id": "agent.runtime.orchestration",
        "agent_version": "1.0.0",
        "capability_id": "capability.runtime.orchestration",
        "input": {"message": "hello"},
        "execution_budget": {"max_tokens": 1_000_000},
    }

    with pytest.raises(PlatformError) as raised:
        await execute_agent_run(
            payload,
            request,
            principal,
            object(),
            contracts,
        )

    assert raised.value.code == "AGENT_RUN_REQUEST_INVALID"
    assert raised.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["GENESIS_TIMEOUT", "GENESIS_UNAVAILABLE"])
async def test_public_run_route_reports_service_unavailability(code: str) -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    agent = await active_agent(contracts, InMemoryAuditRepository())
    principal = Principal(
        actor_id=agent.created_by,
        tenant_id=agent.tenant_id,
        organization_id=agent.organization_id,
        workspace_id=agent.workspace_id,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                agent_registry=SimpleNamespace(get=lambda **kwargs: agent),
                settings=SimpleNamespace(ENABLE_TEST_TOOLS=True, APP_ENV="development"),
            )
        )
    )
    runtime = SimpleNamespace(
        execute=AsyncMock(
            side_effect=GenesisClientError(
                code=code,
                message="transport unavailable",
                correlation_id="corr_failed",
                retryable=True,
            )
        )
    )
    with pytest.raises(PlatformError) as raised:
        await execute_agent_run(
            {
                "agent_id": agent.subject_id,
                "agent_version": agent.version,
                "capability_id": "capability.runtime.orchestration",
                "input": {},
                "execution_mode": "TEST",
            },
            request,
            principal,
            runtime,
            contracts,
        )
    assert raised.value.code == code
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_integration_bootstrap_agent_definition_has_tool_budget() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    registry = AgentRegistry(contracts, audit)
    evidence_registry = SimpleNamespace(
        register=AsyncMock(return_value={"evidence_id": "evidence.test"})
    )
    principal = Principal(
        actor_id="actor_runtime_orchestration",
        tenant_id="tenant_runtime_orchestration",
        organization_id="org_runtime_orchestration",
        workspace_id="workspace_runtime_orchestration",
        permissions=frozenset({"tools.diagnostic.execute"}),
        scopes=frozenset({"scope.diagnostic"}),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                agent_registry=registry,
                evidence_registry=evidence_registry,
                settings=SimpleNamespace(ENABLE_TEST_TOOLS=True, APP_ENV="test"),
            )
        )
    )

    bootstrap = await bootstrap_deterministic_integration(request, principal)  # type: ignore[arg-type]
    assert bootstrap["agent_id"] == "agent.runtime.diagnostic"
    assert bootstrap["agent_version"] == "1.0.0"

    entry = registry.get(
        tenant_id=principal.tenant_id,
        workspace_id=principal.workspace_id,
        subject_id=bootstrap["agent_id"],
        version=bootstrap["agent_version"],
    )
    assert entry.payload["execution_budget"] == {
        "max_tokens": 100,
        "max_steps": 3,
    }
    assert entry.payload["tool_ids"] == ["diagnostic.echo"]


@pytest.mark.asyncio
async def test_orchestrator_derives_tool_budget_when_not_declared_in_agent() -> None:
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

    registry = AgentRegistry(contracts, audit)
    payload = {
        "tenant_id": "tenant_runtime_orchestration",
        "organization_id": "org_runtime_orchestration",
        "workspace_id": "workspace_runtime_orchestration",
        "agent_id": "agent.runtime.no_tool_budget",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_runtime_orchestration",
        "name": "Runtime no tool budget",
        "purpose": "Verify backend derives execution budget for genesis.",
        "risk_level": "LOW",
        "capability_ids": ["capability.runtime.orchestration"],
        "skill_refs": [],
        "model_policy_ref": "policy.runtime-test",
        "tool_ids": ["diagnostic.echo"],
        "permission_refs": ["tools.diagnostic.execute"],
        "scope_refs": ["scope.diagnostic"],
        "execution_budget": {
            "max_tokens": 100,
            "max_steps": 3,
        },
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }
    entry = await registry.register(
        payload,
        tenant_id=payload["tenant_id"],
        organization_id=payload["organization_id"],
        workspace_id=payload["workspace_id"],
        actor_id=payload["owner_actor_id"],
        correlation_id="corr_runtime_registry_010",
    )
    entry = await registry.approve(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_it_authority",
        decision_id="decision.runtime.no_tool_budget",
        authority=DecisionAuthority.IT,
        correlation_id="corr_runtime_registry_011",
    )
    agent = await registry.activate(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_release_authority",
        release_id="release.runtime.no_tool_budget",
        correlation_id="corr_runtime_registry_012",
    )

    correlation_token = correlation_id_context.set("corr_runtime_orchestration_010")
    try:
        completed = await orchestrator.execute(
            {
                "capability_id": "capability.runtime.orchestration",
                "input": {"message": "hello"},
                "requested_tool_ids": ["diagnostic.echo"],
                "scope_refs": ["scope.diagnostic"],
                "execution_mode": "TEST",
            },
            principal=principal,
            agent=agent,
            test_mode_allowed=True,
        )
    finally:
        correlation_id_context.reset(correlation_token)

    assert completed.status is AuthoritativeRunStatus.COMPLETED
    assert genesis.invocation["run_request"]["execution_context"]["execution_budget"] == {
        "max_tokens": 100,
        "max_steps": 3,
        "max_tool_calls": 1,
        "timeout_seconds": 30,
    }


@pytest.mark.asyncio
async def test_integration_bootstrap_denied_in_production() -> None:
    principal = Principal(
        actor_id="actor_prod",
        tenant_id="tenant_prod",
        organization_id="org_prod",
        workspace_id="workspace_prod",
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                agent_registry=None,
                settings=SimpleNamespace(ENABLE_TEST_TOOLS=False, APP_ENV="production"),
            )
        )
    )
    with pytest.raises(PlatformError) as raised:
        await bootstrap_deterministic_integration(request, principal)  # type: ignore[arg-type]

    assert raised.value.code == "INTEGRATION_BOOTSTRAP_DENIED"
    assert raised.value.status_code == 403
