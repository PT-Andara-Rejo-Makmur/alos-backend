"""Typed skill service used by API and backend orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alos.agents.registry import AgentRegistry
from alos.audit import AuditEvent, AuditSink
from alos.identity import Principal
from alos.registry import RegistryNotFoundError, RegistryState
from alos.registry_contracts import RegistryAuthorizationError
from alos.skills.assignment import SkillAssignmentError, SkillAssignmentService
from alos.skills.models import SkillAssignmentRequest, SkillAssignmentResponse
from alos.skills.registry import SkillRegistry


class SkillService:
    def __init__(
        self, *, registry: SkillRegistry, agents: AgentRegistry, audit: AuditSink | None = None
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._agents = agents
        self._assignments = SkillAssignmentService(registry=registry, agents=agents, audit=audit)

    async def register_skill(
        self,
        *,
        payload: dict[str, Any],
        principal: Principal,
        correlation_id: str,
    ) -> dict[str, Any]:
        if not principal.active:
            raise SkillAssignmentError("ASSIGNMENT_NOT_AUTHORIZED", "Principal is inactive.")
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
                    occurred_at=datetime.now(UTC),
                    reason="Skill definition was registered.",
                    metadata={"version": entry.version},
                )
            )
        return dict(entry.payload)

    def list_skills(self, *, principal: Principal) -> list[dict[str, Any]]:
        entries = self._registry.list_authorized_entries(principal=principal)
        results: list[dict[str, Any]] = []
        for item in entries:
            projection = {
                "skill_id": item.subject_id,
                "skill_version": item.version,
                "name": item.payload.get("name"),
                "description": item.payload.get("description"),
                "lifecycle_state": item.state.value,
            }
            owner_actor_id = item.payload.get("owner_actor_id")
            if owner_actor_id is not None:
                projection["owner_actor_id"] = owner_actor_id
            risk_level = item.payload.get("risk_level")
            if risk_level is not None:
                projection["risk_level"] = risk_level
            results.append(projection)
        return results

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
        self._registry.get_authorized(
            principal=principal,
            subject_id=skill_id,
            version=target_version,
        )
        result = dict(entry.payload)
        result.pop("tool_ids", None)
        result.update(lifecycle_state=entry.state.value, correlation_id=entry.correlation_id)
        return result

    def get_versions(self, *, principal: Principal, skill_id: str) -> list[str]:
        return [
            entry.version
            for entry in self._registry.list_authorized_entries(principal=principal)
            if entry.subject_id == skill_id
        ]

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
            agent_version=request.agent_version,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            proposed_agent_version=request.proposed_agent_version,
            principal=principal,
            correlation_id=correlation_id,
        )

    def list_agent_skills(
        self, *, principal: Principal, agent_id: str, correlation_id: str
    ) -> dict[str, Any]:
        entries = self._agents.list_entries(
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            subject_id=agent_id,
        )
        visible = []
        for candidate in entries:
            if (
                candidate.state is RegistryState.DRAFT
                and candidate.created_by == principal.actor_id
            ):
                visible.append(candidate)
            elif candidate.state is RegistryState.ACTIVE:
                try:
                    self._agents.get_authorized(
                        principal=principal,
                        subject_id=candidate.subject_id,
                        version=candidate.version,
                    )
                except RegistryAuthorizationError:
                    continue
                else:
                    visible.append(candidate)
        if not visible:
            raise RegistryNotFoundError("authorized agent definition was not found")
        entry = visible[-1]
        return {
            "agent_id": agent_id,
            "agent_version": entry.version,
            "lifecycle_state": entry.state.value,
            "skill_refs": list(entry.payload.get("skill_refs", [])),
            "correlation_id": correlation_id,
        }

    def _latest_version(self, *, principal: Principal, skill_id: str) -> str:
        versions = self.get_versions(principal=principal, skill_id=skill_id)
        if not versions:
            raise RegistryNotFoundError("skill definition was not found")
        return versions[-1]
