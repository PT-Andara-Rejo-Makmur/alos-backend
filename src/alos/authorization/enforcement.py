"""Unified authorization enforcement façade for MVP2 command boundaries.

Combines AuthorizationPolicy, PermissionRegistry, and audit recording
into a single entry point. Returns typed decisions and rejects
frontend-supplied actor/scope that conflicts with the authenticated principal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from alos.audit import AuditEvent, AuditSink
from alos.authorization.policy import AuthorizationPolicy
from alos.identity import Principal
from alos.permissions import PermissionRegistry


class AuthorizationOutcome(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    NEEDS_INFO = "NEEDS_INFO"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    outcome: AuthorizationOutcome
    principal: Principal | None
    reason: str
    correlation_id: str
    denied_field: str | None = None

    @property
    def is_allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class AuthorizationEnforcer:
    """Backend-authoritative enforcement point for all material commands.

    Every check is fail-closed: missing, inactive, or conflicting identity
    information results in a denial. Frontend-supplied actor/scope values
    are validated against the authenticated principal and rejected on mismatch.
    """

    def __init__(
        self,
        *,
        policy: AuthorizationPolicy,
        permissions: PermissionRegistry,
        audit: AuditSink,
    ) -> None:
        self._policy = policy
        self._permissions = permissions
        self._audit = audit

    async def enforce(
        self,
        *,
        principal: Principal | None,
        required_permission: str,
        required_scopes: frozenset[str] = frozenset(),
        division_id: str | None = None,
        project_id: str | None = None,
        correlation_id: str,
        command: str = "unknown",
        untrusted_actor_id: str | None = None,
        untrusted_tenant_id: str | None = None,
        untrusted_workspace_id: str | None = None,
        untrusted_division_id: str | None = None,
        untrusted_project_id: str | None = None,
    ) -> AuthorizationDecision:
        """Evaluate and record an authorization decision.

        Untrusted fields (from FE/request body) are compared against the
        authenticated principal. Any mismatch is an immediate denial.
        """

        # --- Missing principal = 401 ---
        if principal is None:
            decision = AuthorizationDecision(
                outcome=AuthorizationOutcome.DENIED,
                principal=None,
                reason="Authenticated principal is missing.",
                correlation_id=correlation_id,
                denied_field="actor",
            )
            await self._audit_decision(decision, command)
            return decision

        if not principal.active:
            decision = AuthorizationDecision(
                outcome=AuthorizationOutcome.DENIED,
                principal=principal,
                reason="Authenticated principal is inactive.",
                correlation_id=correlation_id,
                denied_field="actor",
            )
            await self._audit_decision(decision, command)
            return decision

        # --- Reject FE-supplied identity conflicts ---
        conflict = self._check_untrusted_conflicts(
            principal,
            untrusted_actor_id=untrusted_actor_id,
            untrusted_tenant_id=untrusted_tenant_id,
            untrusted_workspace_id=untrusted_workspace_id,
            untrusted_division_id=untrusted_division_id,
            untrusted_project_id=untrusted_project_id,
        )
        if conflict is not None:
            decision = AuthorizationDecision(
                outcome=AuthorizationOutcome.DENIED,
                principal=principal,
                reason=conflict[0],
                correlation_id=correlation_id,
                denied_field=conflict[1],
            )
            await self._audit_decision(decision, command)
            return decision

        # --- Scope completeness check (NEEDS_INFO) ---
        effective_division = division_id or principal.division_id
        effective_project = project_id or principal.project_id
        if not principal.scopes:
            decision = AuthorizationDecision(
                outcome=AuthorizationOutcome.NEEDS_INFO,
                principal=principal,
                reason="At least one Backend-authorized scope is required.",
                correlation_id=correlation_id,
                denied_field="scopes",
            )
            await self._audit_decision(decision, command)
            return decision

        # --- Policy evaluation ---
        allowed = self._policy.is_allowed(
            principal,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            required_permission=required_permission,
            required_scopes=required_scopes,
            division_id=effective_division,
            project_id=effective_project,
        )

        if not allowed:
            decision = AuthorizationDecision(
                outcome=AuthorizationOutcome.DENIED,
                principal=principal,
                reason="Authorization policy denied the request.",
                correlation_id=correlation_id,
                denied_field="policy",
            )
            await self._audit_decision(decision, command)
            return decision

        decision = AuthorizationDecision(
            outcome=AuthorizationOutcome.ALLOWED,
            principal=principal,
            reason="Authorized.",
            correlation_id=correlation_id,
        )
        await self._audit_decision(decision, command)
        return decision

    @staticmethod
    def _check_untrusted_conflicts(
        principal: Principal,
        *,
        untrusted_actor_id: str | None,
        untrusted_tenant_id: str | None,
        untrusted_workspace_id: str | None,
        untrusted_division_id: str | None,
        untrusted_project_id: str | None,
    ) -> tuple[str, str] | None:
        """Compare frontend-supplied values against the authenticated principal.

        Returns (reason, field_name) on conflict, or None if clean.
        """
        checks: list[tuple[str | None, str, str]] = [
            (untrusted_actor_id, principal.actor_id, "actor_id"),
            (untrusted_tenant_id, principal.tenant_id, "tenant_id"),
            (untrusted_workspace_id, principal.workspace_id, "workspace_id"),
        ]
        for untrusted_value, authoritative_value, field_name in checks:
            if untrusted_value is not None and untrusted_value != authoritative_value:
                return (
                    f"Frontend-supplied {field_name} conflicts with authenticated principal.",
                    field_name,
                )

        if untrusted_division_id is not None and principal.division_id is not None:
            if untrusted_division_id != principal.division_id:
                return (
                    "Frontend-supplied division_id conflicts with authenticated principal.",
                    "division_id",
                )
        if untrusted_project_id is not None and principal.project_id is not None:
            if untrusted_project_id != principal.project_id:
                return (
                    "Frontend-supplied project_id conflicts with authenticated principal.",
                    "project_id",
                )
        return None

    async def _audit_decision(self, decision: AuthorizationDecision, command: str) -> None:
        metadata: dict[str, Any] = {"command": command}
        if decision.denied_field:
            metadata["denied_field"] = decision.denied_field
        await self._audit.append(
            AuditEvent(
                event_type="authorization.decision",
                entity_type="command",
                entity_id=command,
                tenant_id=decision.principal.tenant_id if decision.principal else "unknown",
                organization_id=(
                    decision.principal.organization_id if decision.principal else "unknown"
                ),
                workspace_id=(decision.principal.workspace_id if decision.principal else "unknown"),
                actor_id=decision.principal.actor_id if decision.principal else "unknown",
                correlation_id=decision.correlation_id,
                outcome=decision.outcome.value,
                occurred_at=datetime.now(UTC),
                reason=decision.reason,
                metadata=metadata,
            )
        )
