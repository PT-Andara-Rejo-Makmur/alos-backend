from collections.abc import Mapping
from typing import Any

import pytest

from alos.authorization import AuthorizationPolicy
from alos.identity import Principal
from alos.tools.adapters.diagnostic import DiagnosticEchoAdapter
from alos.tools.executor.service import InMemoryToolAuditSink, ToolExecutor
from alos.tools.registry import (
    IdempotencyPolicy,
    ToolLifecycleState,
    ToolRegistration,
    ToolRegistry,
    ToolRegistryConflictError,
)


class AcceptingContractValidator:
    def validate_request(self, payload: Mapping[str, Any]) -> None:
        assert payload["tool_call_id"]

    def validate_result(self, payload: Mapping[str, Any]) -> None:
        assert payload["correlation_id"]


def tool_request(tool_id: str = "diagnostic.echo") -> dict[str, Any]:
    return {
        "tool_call_id": "toolcall_001",
        "run_id": "run_001",
        "tool_id": tool_id,
        "execution_context": {
            "tenant_id": "tenant_001",
            "organization_id": "org_001",
            "workspace_id": "workspace_001",
            "actor_id": "actor_001",
            "permission_refs": ["tools.diagnostic.execute"],
            "scope_refs": ["scope.diagnostic"],
            "correlation_id": "corr_tool_001",
        },
        "arguments": {"message": "hello"},
    }


def principal() -> Principal:
    return Principal(
        actor_id="actor_001",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        permissions=frozenset({"tools.diagnostic.execute"}),
        scopes=frozenset({"scope.diagnostic"}),
    )


def executor(registry: ToolRegistry, audit: InMemoryToolAuditSink) -> ToolExecutor:
    return ToolExecutor(
        contract_validator=AcceptingContractValidator(),
        authorization=AuthorizationPolicy(),
        registry=registry,
        audit_sink=audit,
        production=False,
    )


@pytest.mark.asyncio
async def test_tool_executor_returns_denied_for_unknown_tool() -> None:
    audit = InMemoryToolAuditSink()
    outcome = await executor(ToolRegistry(), audit).execute(
        tool_request("diagnostic.unknown"), principal=principal()
    )

    assert outcome.result["status"] == "DENIED"
    assert outcome.result["error"]["code"] == "TOOL_UNKNOWN"
    assert [record.outcome for record in audit.records] == ["REQUESTED", "DENIED"]


@pytest.mark.asyncio
async def test_tool_executor_propagates_correlation_id_and_audits_success() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="diagnostic.echo",
            required_permission="tools.diagnostic.execute",
            required_scopes=frozenset({"scope.diagnostic"}),
            adapter=DiagnosticEchoAdapter(),
            production_enabled=False,
        )
    )
    audit = InMemoryToolAuditSink()

    outcome = await executor(registry, audit).execute(tool_request(), principal=principal())

    assert outcome.correlation_id == "corr_tool_001"
    assert outcome.result["correlation_id"] == "corr_tool_001"
    assert outcome.result["status"] == "SUCCESS"
    assert outcome.result["output"]["label"] == "NON-PRODUCTION TEST TOOL"
    assert [record.outcome for record in audit.records] == ["REQUESTED", "SUCCESS"]


@pytest.mark.asyncio
async def test_diagnostic_echo_allows_wait_for_cancellation_and_rejects_unknown() -> None:
    from alos.tools.adapters.base import ToolInputError

    adapter = DiagnosticEchoAdapter()
    adapter.validate_arguments({"message": "valid"})
    adapter.validate_arguments({"message": "valid", "wait_for_cancellation": True})
    with pytest.raises(ToolInputError, match="accepts only the message field"):
        adapter.validate_arguments({"message": "valid", "extra": "invalid"})
    with pytest.raises(ToolInputError, match="accepts only the message field"):
        adapter.validate_arguments({"wait_for_cancellation": True})
    with pytest.raises(ToolInputError, match="must be a boolean"):
        adapter.validate_arguments({"message": "valid", "wait_for_cancellation": "not-bool"})



class CountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def validate_arguments(self, _arguments: Mapping[str, Any]) -> None:
        return None

    async def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> Any:
        del execution_context
        self.calls += 1
        return {"value": arguments["message"], "calls": self.calls}


@pytest.mark.asyncio
async def test_required_idempotency_replays_and_rejects_payload_substitution() -> None:
    adapter = CountingAdapter()
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="diagnostic.echo",
            required_permission="tools.diagnostic.execute",
            required_scopes=frozenset({"scope.diagnostic"}),
            adapter=adapter,
            idempotency_policy=IdempotencyPolicy.REQUIRED,
        )
    )
    audit = InMemoryToolAuditSink()
    service = executor(registry, audit)
    missing = await service.execute(tool_request(), principal=principal())
    assert missing.result["status"] == "REJECTED"
    assert missing.result["error"]["code"] == "IDEMPOTENCY_REQUIRED"

    first_request = tool_request()
    first_request["idempotency_key"] = "idempotency-001"
    first = await service.execute(first_request, principal=principal())
    replay_request = tool_request()
    replay_request["idempotency_key"] = "idempotency-001"
    replay_request["execution_context"]["correlation_id"] = "corr_tool_replay_002"
    replay = await service.execute(replay_request, principal=principal())

    assert first.result["status"] == "SUCCESS"
    assert replay.result["output"] == first.result["output"]
    assert replay.result["correlation_id"] == "corr_tool_replay_002"
    assert adapter.calls == 1

    conflict_request = tool_request()
    conflict_request["idempotency_key"] = "idempotency-001"
    conflict_request["arguments"] = {"message": "different payload"}
    conflict = await service.execute(conflict_request, principal=principal())
    assert conflict.result["status"] == "REJECTED"
    assert conflict.result["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert adapter.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registration", "expected_code"),
    [
        (
            ToolRegistration(
                tool_id="diagnostic.echo",
                required_permission="tools.diagnostic.execute",
                required_scopes=frozenset({"scope.diagnostic"}),
                adapter=DiagnosticEchoAdapter(),
                lifecycle_state=ToolLifecycleState.SUSPENDED,
            ),
            "TOOL_NOT_ACTIVE",
        ),
        (
            ToolRegistration(
                tool_id="diagnostic.echo",
                required_permission="tools.diagnostic.execute",
                required_scopes=frozenset({"scope.diagnostic"}),
                adapter=DiagnosticEchoAdapter(),
                kill_switch_active=True,
            ),
            "TOOL_KILL_SWITCH_ACTIVE",
        ),
    ],
)
async def test_tool_lifecycle_and_kill_switch_deny_execution(
    registration: ToolRegistration,
    expected_code: str,
) -> None:
    registry = ToolRegistry()
    registry.register(registration)
    outcome = await executor(registry, InMemoryToolAuditSink()).execute(
        tool_request(), principal=principal()
    )

    assert outcome.result["status"] == "DENIED"
    assert outcome.result["error"]["code"] == expected_code


@pytest.mark.asyncio
async def test_tool_registry_lifecycle_requires_independent_approval() -> None:
    from alos.audit import InMemoryAuditRepository

    audit = InMemoryAuditRepository()
    registry = ToolRegistry(audit)
    await registry.register_draft(
        ToolRegistration(
            tool_id="diagnostic.echo",
            required_permission="tools.diagnostic.execute",
            required_scopes=frozenset({"scope.diagnostic"}),
            adapter=DiagnosticEchoAdapter(),
            lifecycle_state=ToolLifecycleState.DRAFT,
            owner_actor_id="actor_maker_001",
            tenant_id="tenant_001",
            organization_id="org_001",
            workspace_id="workspace_001",
        ),
        actor_id="actor_maker_001",
        correlation_id="corr_tool_lifecycle_001",
    )

    with pytest.raises(ToolRegistryConflictError, match="maker"):
        await registry.approve(
            "diagnostic.echo",
            actor_id="actor_maker_001",
            correlation_id="corr_tool_lifecycle_001",
        )

    approved = await registry.approve(
        "diagnostic.echo",
        actor_id="actor_checker_001",
        correlation_id="corr_tool_lifecycle_001",
    )
    active = await registry.activate(
        "diagnostic.echo",
        actor_id="actor_release_001",
        correlation_id="corr_tool_lifecycle_001",
    )
    disabled = await registry.activate_kill_switch(
        "diagnostic.echo",
        actor_id="actor_it_001",
        reason="Contain diagnostic anomaly.",
        correlation_id="corr_tool_lifecycle_001",
    )
    restored = await registry.clear_kill_switch(
        "diagnostic.echo",
        actor_id="actor_it_001",
        reason="Diagnostic remediation verified.",
        correlation_id="corr_tool_lifecycle_001",
    )
    retired = await registry.retire(
        "diagnostic.echo",
        actor_id="actor_release_001",
        correlation_id="corr_tool_lifecycle_001",
    )

    assert approved.lifecycle_state is ToolLifecycleState.APPROVED
    assert active.lifecycle_state is ToolLifecycleState.ACTIVE
    assert disabled.kill_switch_active is True
    assert restored.kill_switch_active is False
    assert retired.lifecycle_state is ToolLifecycleState.RETIRED
    assert retired.allowlisted is False
    assert len(audit.list_events(tenant_id="tenant_001")) == 6
