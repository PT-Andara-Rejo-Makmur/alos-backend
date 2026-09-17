"""Authoritative ToolExecutor pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from alos.authorization import AuthorizationPolicy
from alos.identity import Principal
from alos.security.errors import PlatformError
from alos.tools.contracts import ToolContractValidator
from alos.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    correlation_id: str
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolAuditRecord:
    tool_call_id: str
    run_id: str
    tool_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    correlation_id: str
    outcome: str
    occurred_at: datetime


class ToolAuditSink(Protocol):
    async def append(self, record: ToolAuditRecord) -> None: ...


class InMemoryToolAuditSink:
    """Test/local sink. Production composition must provide the SQL audit repository."""

    def __init__(self) -> None:
        self.records: list[ToolAuditRecord] = []

    async def append(self, record: ToolAuditRecord) -> None:
        self.records.append(record)


class ToolExecutor:
    def __init__(
        self,
        *,
        contract_validator: ToolContractValidator,
        authorization: AuthorizationPolicy,
        registry: ToolRegistry,
        audit_sink: ToolAuditSink,
        production: bool,
    ) -> None:
        self._contract_validator = contract_validator
        self._authorization = authorization
        self._registry = registry
        self._audit_sink = audit_sink
        self._production = production

    async def execute(
        self,
        request: Mapping[str, Any],
        *,
        principal: Principal | None,
    ) -> ToolExecutionOutcome:
        self._contract_validator.validate_request(request)
        context = request["execution_context"]
        assert isinstance(context, Mapping)
        tenant_id = str(context["tenant_id"])
        workspace_id = str(context["workspace_id"])
        correlation_id = str(context["correlation_id"])
        requested_scopes = frozenset(str(item) for item in context.get("scope_refs", []))
        tool_id = str(request["tool_id"])

        registration = self._registry.get(tool_id)
        if registration is None:
            await self._audit(request, principal, correlation_id, "DENIED")
            raise PlatformError(
                "TOOL_NOT_ALLOWED",
                "The requested tool is not allowlisted.",
                status_code=403,
                correlation_id=correlation_id,
            )
        if self._production and not registration.production_enabled:
            await self._audit(request, principal, correlation_id, "DENIED")
            raise PlatformError(
                "TEST_TOOL_DISABLED",
                "Non-production tools are disabled in production.",
                status_code=403,
                correlation_id=correlation_id,
            )
        if not registration.required_scopes.issubset(requested_scopes):
            await self._audit(request, principal, correlation_id, "DENIED")
            raise PlatformError(
                "SCOPE_DENIED",
                "ToolRequest does not declare all required scopes.",
                status_code=403,
                correlation_id=correlation_id,
            )
        if not self._authorization.is_allowed(
            principal,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            required_permission=registration.required_permission,
            required_scopes=registration.required_scopes,
        ):
            await self._audit(request, principal, correlation_id, "DENIED")
            raise PlatformError(
                "AUTHORIZATION_DENIED",
                "Tenant, workspace, permission, or scope policy denied execution.",
                status_code=403,
                correlation_id=correlation_id,
            )

        arguments = request["arguments"]
        assert isinstance(arguments, Mapping)
        try:
            output = await registration.adapter.execute(arguments)
        except Exception as exc:
            await self._audit(request, principal, correlation_id, "FAILED")
            raise PlatformError(
                "TOOL_EXECUTION_FAILED",
                "The tool adapter failed.",
                status_code=502,
                retryable=False,
                correlation_id=correlation_id,
            ) from exc

        await self._audit(request, principal, correlation_id, "COMPLETED")
        result = {
            "tool_call_id": str(request["tool_call_id"]),
            "run_id": str(request["run_id"]),
            "status": "COMPLETED",
            "output": output,
            "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        return ToolExecutionOutcome(correlation_id=correlation_id, result=result)

    async def _audit(
        self,
        request: Mapping[str, Any],
        principal: Principal | None,
        correlation_id: str,
        outcome: str,
    ) -> None:
        context = request["execution_context"]
        assert isinstance(context, Mapping)
        await self._audit_sink.append(
            ToolAuditRecord(
                tool_call_id=str(request["tool_call_id"]),
                run_id=str(request["run_id"]),
                tool_id=str(request["tool_id"]),
                tenant_id=str(context["tenant_id"]),
                workspace_id=str(context["workspace_id"]),
                actor_id=principal.actor_id if principal else "actor_unknown",
                correlation_id=correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
            )
        )
