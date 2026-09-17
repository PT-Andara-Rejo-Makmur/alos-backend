from collections.abc import Mapping
from typing import Any

import pytest

from alos.authorization import AuthorizationPolicy
from alos.identity import Principal
from alos.security.errors import PlatformError
from alos.tools.adapters.diagnostic import DiagnosticEchoAdapter
from alos.tools.executor.service import InMemoryToolAuditSink, ToolExecutor
from alos.tools.registry import ToolRegistration, ToolRegistry


class AcceptingContractValidator:
    def validate_request(self, payload: Mapping[str, Any]) -> None:
        assert payload["tool_call_id"]


def tool_request(tool_id: str = "tool_diagnostic_echo") -> dict[str, Any]:
    return {
        "tool_call_id": "toolcall_001",
        "run_id": "run_001",
        "tool_id": tool_id,
        "execution_context": {
            "tenant_id": "tenant_001",
            "workspace_id": "workspace_001",
            "scope_refs": ["scope.diagnostic"],
            "correlation_id": "corr_tool_001",
        },
        "arguments": {"message": "hello"},
    }


def principal() -> Principal:
    return Principal(
        actor_id="actor_001",
        tenant_id="tenant_001",
        workspace_id="workspace_001",
        permissions=frozenset({"tools.diagnostic.execute"}),
        scopes=frozenset({"scope.diagnostic"}),
    )


@pytest.mark.asyncio
async def test_tool_executor_rejects_unknown_tool() -> None:
    audit = InMemoryToolAuditSink()
    executor = ToolExecutor(
        contract_validator=AcceptingContractValidator(),
        authorization=AuthorizationPolicy(),
        registry=ToolRegistry(),
        audit_sink=audit,
        production=False,
    )
    with pytest.raises(PlatformError, match="not allowlisted") as raised:
        await executor.execute(tool_request("tool_unknown"), principal=principal())
    assert raised.value.code == "TOOL_NOT_ALLOWED"
    assert audit.records[0].outcome == "DENIED"


@pytest.mark.asyncio
async def test_tool_executor_propagates_correlation_id_and_audits() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="tool_diagnostic_echo",
            required_permission="tools.diagnostic.execute",
            required_scopes=frozenset({"scope.diagnostic"}),
            adapter=DiagnosticEchoAdapter(),
            production_enabled=False,
        )
    )
    audit = InMemoryToolAuditSink()
    executor = ToolExecutor(
        contract_validator=AcceptingContractValidator(),
        authorization=AuthorizationPolicy(),
        registry=registry,
        audit_sink=audit,
        production=False,
    )
    outcome = await executor.execute(tool_request(), principal=principal())
    assert outcome.correlation_id == "corr_tool_001"
    assert outcome.result["status"] == "COMPLETED"
    assert outcome.result["output"]["label"] == "NON-PRODUCTION TEST TOOL"
    assert audit.records[0].correlation_id == "corr_tool_001"
