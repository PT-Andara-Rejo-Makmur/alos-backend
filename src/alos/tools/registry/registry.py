from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from alos.audit import AuditEvent, AuditSink
from alos.tools.adapters.base import ToolAdapter


class ToolLifecycleState(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class IdempotencyPolicy(StrEnum):
    NONE = "NONE"
    OPTIONAL = "OPTIONAL"
    REQUIRED = "REQUIRED"


class ToolRegistryConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    tool_id: str
    required_permission: str
    required_scopes: frozenset[str]
    adapter: ToolAdapter
    allowlisted: bool = True
    production_enabled: bool = True
    timeout_seconds: float = 5.0
    lifecycle_state: ToolLifecycleState = ToolLifecycleState.ACTIVE
    idempotency_policy: IdempotencyPolicy = IdempotencyPolicy.OPTIONAL
    kill_switch_active: bool = False
    owner_actor_id: str | None = None
    approved_by: str | None = None
    tenant_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None


class ToolRegistry:
    def __init__(self, audit: AuditSink | None = None) -> None:
        self._tools: dict[str, ToolRegistration] = {}
        self._audit = audit

    def register(self, registration: ToolRegistration) -> None:
        if registration.tool_id in self._tools:
            raise ValueError(f"Tool already registered: {registration.tool_id}")
        self._tools[registration.tool_id] = registration

    async def register_draft(
        self,
        registration: ToolRegistration,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> ToolRegistration:
        if registration.lifecycle_state is not ToolLifecycleState.DRAFT:
            raise ToolRegistryConflictError("authoritative registration must start as DRAFT")
        if registration.owner_actor_id != actor_id:
            raise ToolRegistryConflictError("draft owner must match the registering actor")
        if registration.tool_id in self._tools:
            raise ToolRegistryConflictError("tool is already registered")
        await self._record(
            registration,
            actor_id,
            correlation_id,
            "tool.draft_registered",
            "DRAFT",
        )
        self._tools[registration.tool_id] = registration
        return registration

    def get(self, tool_id: str) -> ToolRegistration | None:
        return self._tools.get(tool_id)

    async def approve(
        self, tool_id: str, *, actor_id: str, correlation_id: str
    ) -> ToolRegistration:
        current = self._require(tool_id)
        if current.lifecycle_state is not ToolLifecycleState.DRAFT:
            raise ToolRegistryConflictError("only a DRAFT tool can be approved")
        if current.owner_actor_id == actor_id:
            raise ToolRegistryConflictError("tool maker cannot approve their own tool")
        updated = replace(
            current,
            lifecycle_state=ToolLifecycleState.APPROVED,
            approved_by=actor_id,
        )
        await self._record(updated, actor_id, correlation_id, "tool.approved", "APPROVED")
        self._tools[tool_id] = updated
        return updated

    async def activate(
        self, tool_id: str, *, actor_id: str, correlation_id: str
    ) -> ToolRegistration:
        current = self._require(tool_id)
        if current.lifecycle_state is not ToolLifecycleState.APPROVED:
            raise ToolRegistryConflictError("only an APPROVED tool can be activated")
        updated = replace(current, lifecycle_state=ToolLifecycleState.ACTIVE)
        await self._record(updated, actor_id, correlation_id, "tool.activated", "ACTIVE")
        self._tools[tool_id] = updated
        return updated

    async def suspend(
        self, tool_id: str, *, actor_id: str, correlation_id: str
    ) -> ToolRegistration:
        current = self._require(tool_id)
        if current.lifecycle_state is not ToolLifecycleState.ACTIVE:
            raise ToolRegistryConflictError("only an ACTIVE tool can be suspended")
        updated = replace(current, lifecycle_state=ToolLifecycleState.SUSPENDED)
        await self._record(updated, actor_id, correlation_id, "tool.suspended", "SUSPENDED")
        self._tools[tool_id] = updated
        return updated

    async def retire(self, tool_id: str, *, actor_id: str, correlation_id: str) -> ToolRegistration:
        current = self._require(tool_id)
        if current.lifecycle_state is ToolLifecycleState.RETIRED:
            raise ToolRegistryConflictError("tool is already retired")
        updated = replace(
            current,
            lifecycle_state=ToolLifecycleState.RETIRED,
            allowlisted=False,
        )
        await self._record(updated, actor_id, correlation_id, "tool.retired", "RETIRED")
        self._tools[tool_id] = updated
        return updated

    async def activate_kill_switch(
        self,
        tool_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> ToolRegistration:
        current = self._require(tool_id)
        if current.lifecycle_state is not ToolLifecycleState.ACTIVE:
            raise ToolRegistryConflictError("kill switch requires an ACTIVE tool")
        if not reason.strip():
            raise ToolRegistryConflictError("kill switch reason is required")
        updated = replace(current, kill_switch_active=True)
        await self._record(
            updated,
            actor_id,
            correlation_id,
            "tool.kill_switch_activated",
            "SUSPENDED",
            reason=reason,
        )
        self._tools[tool_id] = updated
        return updated

    async def clear_kill_switch(
        self,
        tool_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> ToolRegistration:
        current = self._require(tool_id)
        if not current.kill_switch_active:
            raise ToolRegistryConflictError("there is no active tool kill switch")
        if not reason.strip():
            raise ToolRegistryConflictError("kill switch clear reason is required")
        updated = replace(current, kill_switch_active=False)
        await self._record(
            updated,
            actor_id,
            correlation_id,
            "tool.kill_switch_cleared",
            "ACTIVE",
            reason=reason,
        )
        self._tools[tool_id] = updated
        return updated

    def _require(self, tool_id: str) -> ToolRegistration:
        registration = self._tools.get(tool_id)
        if registration is None:
            raise LookupError("tool is not registered")
        return registration

    async def _record(
        self,
        registration: ToolRegistration,
        actor_id: str,
        correlation_id: str,
        event_type: str,
        outcome: str,
        *,
        reason: str = "Authoritative Tool Registry lifecycle transition",
    ) -> None:
        if self._audit is None:
            raise ToolRegistryConflictError(
                "authoritative tool lifecycle transition requires an audit sink"
            )
        if not all(
            (
                registration.tenant_id,
                registration.organization_id,
                registration.workspace_id,
            )
        ):
            raise ToolRegistryConflictError(
                "authoritative tool lifecycle transition requires tenant context"
            )
        await self._audit.append(
            AuditEvent(
                event_type=event_type,
                entity_type="tool_definition",
                entity_id=registration.tool_id,
                tenant_id=registration.tenant_id or "",
                organization_id=registration.organization_id or "",
                workspace_id=registration.workspace_id or "",
                actor_id=actor_id,
                correlation_id=correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                reason=reason,
            )
        )
