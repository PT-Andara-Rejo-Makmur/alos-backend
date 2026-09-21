"""Backend-owned skill assignment validation and assignment storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alos.audit import AuditEvent, AuditSink
from alos.identity import Principal
from alos.registry import RegistryNotFoundError, RegistryState
from alos.skills.models import SkillAssignmentResponse
from alos.skills.registry import SkillRegistry


class SkillAssignmentError(ValueError):
    pass


class SkillAssignmentService:
    def __init__(self, *, registry: SkillRegistry, audit: AuditSink | None = None) -> None:
        self._registry = registry
        self._audit = audit
        self._assignments: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def _audit_rejection(
        self,
        *,
        principal: Principal,
        agent_id: str,
        skill_id: str,
        skill_version: str,
        correlation_id: str,
        reason: str,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.append(
            AuditEvent(
                event_type="skill.assignment.rejected",
                entity_type="skill_assignment",
                entity_id=f"{agent_id}:{skill_id}:{skill_version}",
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
                outcome="REJECTED",
                occurred_at=datetime.now(UTC),
                reason=reason,
                metadata={
                    "agent_id": agent_id,
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "rejected_by": principal.actor_id,
                },
            )
        )

    async def assign(
        self,
        *,
        agent_id: str,
        skill_id: str,
        skill_version: str,
        principal: Principal,
        correlation_id: str,
        agent_scope: frozenset[str] | None = None,
    ) -> SkillAssignmentResponse:
        if not principal.active:
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Principal is inactive.",
            )
            raise SkillAssignmentError("Principal is inactive.")

        try:
            entry = self._registry.get(
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                subject_id=skill_id,
                version=skill_version,
            )
        except RegistryNotFoundError as exc:
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Skill version does not exist or is not registered.",
            )
            raise SkillAssignmentError(
                "Skill version does not exist or is not registered."
            ) from exc

        if entry.state is not RegistryState.ACTIVE:
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Skill is not ACTIVE and cannot be assigned.",
            )
            raise SkillAssignmentError("Skill is not ACTIVE and cannot be assigned.")

        try:
            authorized = self._registry.get_authorized(
                principal=principal,
                subject_id=skill_id,
                version=skill_version,
            )
        except Exception as exc:
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Skill is not authorized for this principal.",
            )
            raise SkillAssignmentError("Skill is not authorized for this principal.") from exc

        if not set(authorized.permissions).issubset(principal.permissions):
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Principal is missing required skill permissions.",
            )
            raise SkillAssignmentError("Principal is missing required skill permissions.")
        if not set(authorized.scope).issubset(principal.scopes):
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Principal scope is incompatible with the skill scope.",
            )
            raise SkillAssignmentError("Principal scope is incompatible with the skill scope.")
        if agent_scope is not None and not set(authorized.scope).issubset(agent_scope):
            await self._audit_rejection(
                principal=principal,
                agent_id=agent_id,
                skill_id=skill_id,
                skill_version=skill_version,
                correlation_id=correlation_id,
                reason="Agent scope is incompatible with the required skill scope.",
            )
            raise SkillAssignmentError("Agent scope is incompatible with the required skill scope.")

        key = (agent_id, skill_id, skill_version)
        assignment = {
            "agent_id": agent_id,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "assigned_by": principal.actor_id,
            "correlation_id": correlation_id,
            "scope_refs": list(authorized.scope),
            "permission_refs": list(authorized.permissions),
            "assigned_at": datetime.now(UTC),
        }
        self._assignments[key] = assignment

        if self._audit is not None:
            await self._audit.append(
                AuditEvent(
                    event_type="skill.assignment.created",
                    entity_type="skill_assignment",
                    entity_id=f"{agent_id}:{skill_id}:{skill_version}",
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    actor_id=principal.actor_id,
                    correlation_id=correlation_id,
                    outcome="ASSIGNED",
                    occurred_at=datetime.now(UTC),
                    reason="Authorized skill assignment created.",
                    metadata={
                        "skill_id": skill_id,
                        "skill_version": skill_version,
                        "agent_id": agent_id,
                        "scope_refs": list(authorized.scope),
                        "permission_refs": list(authorized.permissions),
                    },
                )
            )

        return SkillAssignmentResponse(
            agent_id=agent_id,
            skill_id=skill_id,
            skill_version=skill_version,
            status="ASSIGNED",
            assigned_by=principal.actor_id,
            correlation_id=correlation_id,
            scope_refs=list(authorized.scope),
            permission_refs=list(authorized.permissions),
        )

    def list_for_agent(self, *, agent_id: str) -> list[dict[str, Any]]:
        matches = [
            assignment
            for (assigned_agent_id, _, _), assignment in self._assignments.items()
            if assigned_agent_id == agent_id
        ]
        return matches
