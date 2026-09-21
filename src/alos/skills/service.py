"""Typed skill service used by API and backend orchestration."""

from __future__ import annotations

from typing import Any

from alos.audit import AuditEvent, AuditSink
from alos.identity import Principal
from alos.registry import RegistryNotFoundError
from alos.skills.assignment import SkillAssignmentError, SkillAssignmentService
from alos.skills.models import SkillAssignmentRequest, SkillAssignmentResponse
from alos.skills.registry import SkillRegistry


class SkillService:
    def __init__(self, *, registry: SkillRegistry, audit: AuditSink | None = None) -> None:
        self._registry = registry
        self._audit = audit
        self._assignments = SkillAssignmentService(registry=registry, audit=audit)

    async def register_skill(
        self,
        *,
        payload: dict[str, Any],
        principal: Principal,
        correlation_id: str,
    ) -> dict[str, Any]:
        if not principal.active:
            raise SkillAssignmentError("Principal is inactive.")
        entry = await self._registry.register(
            payload,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
        )
        if self._audit is not None:
            await self._audit.append(
                AuditEvent(
                    event_type="skill.created",
                    entity_type="skill",
                    entity_id=entry.subject_id,
                    tenant_id=entry.tenant_id,
                    organization_id=entry.organization_id,
                    workspace_id=entry.workspace_id,
                    actor_id=principal.actor_id,
                    correlation_id=correlation_id,
                    outcome="CREATED",
                    reason="Skill definition was registered.",
                    metadata={"version": entry.version},
                )
            )
        return dict(entry.payload)

    def list_skills(self, *, principal: Principal) -> list[dict[str, Any]]:
        entries = self._registry.list_authorized_entries(principal=principal)
        return [
            {
                "skill_id": item.subject_id,
                "skill_version": item.version,
                "name": item.payload.get("name"),
                "description": item.payload.get("description"),
                "status": item.state.value,
                "owner": item.payload.get("owner_actor_id") or item.created_by,
            }
            for item in entries
        ]

    def get_skill(
        self,
        *,
        principal: Principal,
        skill_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        target_version = version or self._latest_version(
            principal=principal,
            skill_id=skill_id,
        )
        entry = self._registry.get(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            subject_id=skill_id,
            version=target_version,
        )
        view = self._registry.get_authorized(
            principal=principal,
            subject_id=skill_id,
            version=target_version,
        )
        return {
            "skill_id": entry.subject_id,
            "skill_version": entry.version,
            "name": entry.payload.get("name"),
            "description": entry.payload.get("description"),
            "purpose": entry.payload.get("purpose"),
            "status": entry.state.value,
            "owner": view.owner,
            "scope_refs": list(view.scope),
            "permission_refs": list(view.permissions),
            "required_tool_ids": list(entry.payload.get("required_tool_ids") or []),
        }

    def get_versions(self, *, principal: Principal, skill_id: str) -> list[str]:
        values = [
            entry.version
            for entry in self._registry._entries.values()  # type: ignore[attr-defined]
            if entry.subject_id == skill_id
            and entry.tenant_id == principal.tenant_id
            and entry.workspace_id == principal.workspace_id
            and entry.organization_id == principal.organization_id
        ]
        return sorted(set(values))

    async def assign_skill(
        self,
        *,
        request: SkillAssignmentRequest,
        principal: Principal,
        correlation_id: str,
        agent_scope: frozenset[str] | None = None,
    ) -> SkillAssignmentResponse:
        return await self._assignments.assign(
            agent_id=request.agent_id,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            principal=principal,
            correlation_id=correlation_id,
            agent_scope=agent_scope,
        )

    def list_agent_skills(self, *, principal: Principal, agent_id: str) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": item["agent_id"],
                "skill_id": item["skill_id"],
                "skill_version": item["skill_version"],
                "scope_refs": item["scope_refs"],
                "permission_refs": item["permission_refs"],
            }
            for item in self._assignments.list_for_agent(agent_id=agent_id)
            if item["scope_refs"] and item["permission_refs"]
        ]

    def _latest_version(self, *, principal: Principal, skill_id: str) -> str:
        versions = self.get_versions(principal=principal, skill_id=skill_id)
        if not versions:
            raise RegistryNotFoundError("skill definition was not found")
        return sorted(versions)[-1]
