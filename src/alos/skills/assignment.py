"""Backend-authoritative skill assignment via immutable AgentDefinition drafts."""

from __future__ import annotations

from datetime import UTC, datetime

from alos.agents.registry import AgentRegistry
from alos.audit import AuditEvent, AuditSink
from alos.identity import Principal
from alos.registry import RegistryConflictError, RegistryNotFoundError, RegistryState
from alos.registry_contracts import RegistryAuthorizationError
from alos.skills.models import SkillAssignmentResponse, SkillVersionRef
from alos.skills.registry import SkillRegistry


class SkillAssignmentError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SkillAssignmentService:
    def __init__(
        self, *, registry: SkillRegistry, agents: AgentRegistry, audit: AuditSink | None = None
    ) -> None:
        self._registry, self._agents, self._audit = registry, agents, audit

    async def assign(
        self,
        *,
        agent_id: str,
        agent_version: str,
        skill_id: str,
        skill_version: str,
        proposed_agent_version: str | None,
        principal: Principal,
        correlation_id: str,
    ) -> SkillAssignmentResponse:
        try:
            agent = self._agents.get(
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                subject_id=agent_id,
                version=agent_version,
            )
        except RegistryNotFoundError as exc:
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Agent version not found.",
            )
            raise SkillAssignmentError(
                "AGENT_VERSION_NOT_FOUND", "Exact AgentDefinition version was not found."
            ) from exc
        if agent.state is not RegistryState.ACTIVE:
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Agent is not ACTIVE.",
            )
            raise SkillAssignmentError("AGENT_NOT_ACTIVE", "Base AgentDefinition must be ACTIVE.")
        try:
            skill_entry = self._registry.get(
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                subject_id=skill_id,
                version=skill_version,
            )
        except RegistryNotFoundError as exc:
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Skill version not found.",
            )
            raise SkillAssignmentError(
                "SKILL_VERSION_NOT_FOUND", "Exact skill version was not found."
            ) from exc
        if skill_entry.state is not RegistryState.ACTIVE:
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Skill is not ACTIVE.",
            )
            raise SkillAssignmentError("SKILL_NOT_ACTIVE", "Skill version must be ACTIVE.")
        try:
            self._agents.get_authorized(
                principal=principal, subject_id=agent_id, version=agent_version
            )
            skill = self._registry.get_authorized(
                principal=principal, subject_id=skill_id, version=skill_version
            )
        except RegistryAuthorizationError as exc:
            await self._record(
                principal, agent_id, skill_id, skill_version, correlation_id, "REJECTED", str(exc)
            )
            message = str(exc).lower()
            code = (
                "SKILL_PERMISSION_MISMATCH"
                if "permission" in message
                else "SKILL_SCOPE_MISMATCH"
                if "scope" in message
                else "ASSIGNMENT_NOT_AUTHORIZED"
            )
            raise SkillAssignmentError(
                code,
                "Exact ACTIVE agent and skill versions must be authorized.",
            ) from exc
        refs = list(agent.payload.get("skill_refs", []))
        reference = {"skill_id": skill_id, "skill_version": skill_version}
        if reference in refs:
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Duplicate skill reference.",
            )
            raise SkillAssignmentError(
                "AGENT_SKILL_DUPLICATE",
                "AgentDefinition already contains this skill reference.",
            )
        agent_permissions = set(agent.payload.get("permission_refs", []))
        if not set(skill.permissions).issubset(agent_permissions):
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Skill permission prerequisites exceed AgentDefinition permissions.",
            )
            raise SkillAssignmentError(
                "SKILL_PERMISSION_MISMATCH",
                "Skill permission prerequisites exceed AgentDefinition permissions.",
            )
        agent_scopes = set(agent.payload.get("scope_refs", []))
        if not set(skill.scope).issubset(agent_scopes):
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Skill scope prerequisites exceed AgentDefinition scope.",
            )
            raise SkillAssignmentError(
                "SKILL_SCOPE_MISMATCH",
                "Skill scope prerequisites exceed AgentDefinition scope.",
            )
        if not set(skill.tools).issubset(set(agent.payload.get("tool_ids", []))):
            await self._record(
                principal,
                agent_id,
                skill_id,
                skill_version,
                correlation_id,
                "REJECTED",
                "Tool prerequisite unmet.",
            )
            raise SkillAssignmentError(
                "SKILL_TOOL_REQUIREMENT_UNMET",
                "AgentDefinition lacks a required skill tool prerequisite.",
            )
        draft_version = proposed_agent_version or self._next_minor(agent_version)
        draft_payload = dict(agent.payload)
        draft_payload.update(agent_version=draft_version, skill_refs=[*refs, reference])
        try:
            draft = await self._agents.register(
                draft_payload,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
            )
        except RegistryConflictError as exc:
            raise SkillAssignmentError(
                "AGENT_VERSION_CONFLICT", "Proposed AgentDefinition version already exists."
            ) from exc
        await self._record(
            principal,
            agent_id,
            skill_id,
            skill_version,
            correlation_id,
            "DRAFT",
            "Immutable AgentDefinition draft created.",
        )
        return SkillAssignmentResponse(
            agent_id=agent_id,
            base_agent_version=agent_version,
            draft_agent_version=draft.version,
            skill_ref=SkillVersionRef(skill_id=skill_id, skill_version=skill_version),
            correlation_id=correlation_id,
        )

    async def _record(
        self,
        principal: Principal,
        agent_id: str,
        skill_id: str,
        skill_version: str,
        correlation_id: str,
        outcome: str,
        reason: str,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.append(
            AuditEvent(
                event_type=(
                    "skill.assignment.draft_created"
                    if outcome == "DRAFT"
                    else "skill.assignment.rejected"
                ),
                entity_type="agent",
                entity_id=agent_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                reason=reason,
                metadata={"skill_id": skill_id, "skill_version": skill_version},
            )
        )

    @staticmethod
    def _next_minor(version: str) -> str:
        parts = version.split("-", 1)[0].split(".")
        if len(parts) != 3 or not all(item.isdigit() for item in parts):
            raise SkillAssignmentError(
                "AGENT_VERSION_INVALID", "Agent version is not semantic-version compatible."
            )
        return f"{parts[0]}.{int(parts[1]) + 1}.0"
