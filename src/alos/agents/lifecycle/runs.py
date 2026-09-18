"""Backend-owned authoritative Agent run lifecycle and audit boundary."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from alos.audit import AuditEvent, AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import RegistryEntry, RegistryState

AGENT_RUN_REQUEST_SCHEMA = "https://schemas.alos.dev/v1/agent/agent-run-request.schema.json"
AGENT_RUN_RESULT_SCHEMA = "https://schemas.alos.dev/v1/agent/agent-run-result.schema.json"


class RunAuthorityError(ValueError):
    pass


class AuthoritativeRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True, slots=True)
class AuthoritativeRunRecord:
    run_id: str
    root_run_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    actor_id: str
    correlation_id: str
    agent_id: str
    agent_version: str
    capability_id: str
    status: AuthoritativeRunStatus
    registry_digest: str
    lifecycle_authorization: str
    authorized_tool_ids: tuple[str, ...]
    request: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None


class AgentRunStore(Protocol):
    async def create(self, record: AuthoritativeRunRecord) -> None: ...

    async def update(self, record: AuthoritativeRunRecord) -> None: ...

    async def get(self, run_id: str) -> AuthoritativeRunRecord: ...


class InMemoryAgentRunStore:
    """Deterministic local/test implementation of the authoritative persistence port."""

    def __init__(self) -> None:
        self._runs: dict[str, AuthoritativeRunRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: AuthoritativeRunRecord) -> None:
        async with self._lock:
            if record.run_id in self._runs:
                raise RunAuthorityError("run_id already exists")
            self._runs[record.run_id] = AgentRunAuthority._copy(record)

    async def update(self, record: AuthoritativeRunRecord) -> None:
        async with self._lock:
            if record.run_id not in self._runs:
                raise RunAuthorityError("authoritative run was not found")
            self._runs[record.run_id] = AgentRunAuthority._copy(record)

    async def get(self, run_id: str) -> AuthoritativeRunRecord:
        record = self._runs.get(run_id)
        if record is None:
            raise RunAuthorityError("authoritative run was not found")
        return AgentRunAuthority._copy(record)


class AgentRunAuthority:
    """Validate lifecycle/context and retain canonical run state in Backend."""

    def __init__(
        self,
        *,
        contracts: CanonicalContractCatalog,
        audit: AuditSink,
        allow_test_drafts: bool = False,
        store: AgentRunStore | None = None,
    ) -> None:
        self._contracts = contracts
        self._audit = audit
        self._allow_test_drafts = allow_test_drafts
        self._store = store or InMemoryAgentRunStore()

    async def begin(
        self,
        payload: dict[str, Any],
        *,
        agent: RegistryEntry,
    ) -> AuthoritativeRunRecord:
        request = self._contracts.validate(AGENT_RUN_REQUEST_SCHEMA, payload)
        context = request["execution_context"]
        if not isinstance(context, dict):
            raise RunAuthorityError("execution_context must be an object")
        self._require_context(request, context, agent)
        self._require_lifecycle(request, agent)
        self._require_permissions(context, agent)
        self._require_scope(context, agent)
        self._require_tools(request, agent)
        run_id = str(request["run_id"])
        record = AuthoritativeRunRecord(
            run_id=run_id,
            root_run_id=str(request["root_run_id"]),
            tenant_id=str(context["tenant_id"]),
            organization_id=str(context["organization_id"]),
            workspace_id=str(context["workspace_id"]),
            actor_id=str(context["actor_id"]),
            correlation_id=str(context["correlation_id"]),
            agent_id=str(request["agent_id"]),
            agent_version=str(request["agent_version"]),
            capability_id=str(request["capability_id"]),
            status=AuthoritativeRunStatus.RUNNING,
            registry_digest=agent.digest,
            lifecycle_authorization=(
                "ACTIVE" if agent.state is RegistryState.ACTIVE else "TEST_AUTHORIZED"
            ),
            authorized_tool_ids=tuple(
                tool_id
                for tool_id in request.get("requested_tool_ids", [])
                if tool_id in agent.payload.get("tool_ids", [])
            ),
            request=copy.deepcopy(request),
            created_at=datetime.now(UTC),
        )
        await self._store.create(record)
        await self._record_event(record, "run.started")
        return self._copy(record)

    async def complete(self, payload: dict[str, Any]) -> AuthoritativeRunRecord:
        result = self._contracts.validate(AGENT_RUN_RESULT_SCHEMA, payload)
        run_id = str(result["run_id"])
        current = await self._store.get(run_id)
        if current.status is not AuthoritativeRunStatus.RUNNING:
            raise RunAuthorityError("authoritative run is already terminal")
        self._require_result_matches(current, result)
        try:
            target = AuthoritativeRunStatus(str(result["status"]))
        except ValueError as exc:
            raise RunAuthorityError("AgentRunResult is not terminal") from exc
        if target is AuthoritativeRunStatus.RUNNING:
            raise RunAuthorityError("AgentRunResult is not terminal")
        updated = replace(
            current,
            status=target,
            completed_at=datetime.now(UTC),
            result=copy.deepcopy(result),
        )
        await self._store.update(updated)
        event_type = "run.completed" if target is AuthoritativeRunStatus.COMPLETED else "run.failed"
        await self._record_event(updated, event_type)
        return self._copy(updated)

    async def get(self, run_id: str) -> AuthoritativeRunRecord:
        return await self._store.get(run_id)

    async def runtime_authorization(self, run_id: str) -> dict[str, Any]:
        record = await self.get(run_id)
        if record.status is not AuthoritativeRunStatus.RUNNING:
            raise RunAuthorityError("only a running record can be authorized")
        return {
            "run_id": record.run_id,
            "registry_digest": record.registry_digest,
            "lifecycle_state": record.lifecycle_authorization,
            "allowed_tool_ids": list(record.authorized_tool_ids),
        }

    @staticmethod
    def _require_context(
        request: dict[str, Any],
        context: dict[str, Any],
        agent: RegistryEntry,
    ) -> None:
        if (
            context["tenant_id"] != agent.tenant_id
            or context["organization_id"] != agent.organization_id
            or context["workspace_id"] != agent.workspace_id
        ):
            raise RunAuthorityError("run context does not match authoritative registry context")
        if request["agent_id"] != agent.subject_id or request["agent_version"] != agent.version:
            raise RunAuthorityError("run Agent identity/version does not match registry entry")

    def _require_lifecycle(
        self,
        request: dict[str, Any],
        agent: RegistryEntry,
    ) -> None:
        if agent.state is RegistryState.ACTIVE:
            return
        if request.get("execution_mode") == "TEST" and self._allow_test_drafts:
            if agent.state in {RegistryState.DRAFT, RegistryState.APPROVED}:
                return
        raise RunAuthorityError("Agent lifecycle is not authorized for execution")

    @staticmethod
    def _require_permissions(context: dict[str, Any], agent: RegistryEntry) -> None:
        required = frozenset(str(item) for item in agent.payload.get("permission_refs", []))
        granted = frozenset(str(item) for item in context.get("permission_refs", []))
        if not required.issubset(granted):
            raise RunAuthorityError("execution context lacks Agent permissions")

    @staticmethod
    def _require_scope(context: dict[str, Any], agent: RegistryEntry) -> None:
        required = frozenset(str(item) for item in agent.payload.get("scope_refs", []))
        granted = frozenset(str(item) for item in context.get("scope_refs", []))
        if required and not required.issubset(granted):
            raise RunAuthorityError("execution context lacks Agent scope")

    @staticmethod
    def _require_tools(request: dict[str, Any], agent: RegistryEntry) -> None:
        requested = frozenset(str(item) for item in request.get("requested_tool_ids", []))
        allowed = frozenset(str(item) for item in agent.payload.get("tool_ids", []))
        if not requested.issubset(allowed):
            raise RunAuthorityError("AgentRunRequest contains a tool outside Agent definition")

    @staticmethod
    def _require_result_matches(
        current: AuthoritativeRunRecord,
        result: dict[str, Any],
    ) -> None:
        expected = {
            "root_run_id": current.root_run_id,
            "correlation_id": current.correlation_id,
            "agent_id": current.agent_id,
            "agent_version": current.agent_version,
            "capability_id": current.capability_id,
        }
        if any(result.get(key) != value for key, value in expected.items()):
            raise RunAuthorityError("AgentRunResult does not match authoritative run identity")

    async def _record_event(self, record: AuthoritativeRunRecord, event_type: str) -> None:
        await self._audit.append(
            AuditEvent(
                event_type=event_type,
                entity_type="agent_run",
                entity_id=record.run_id,
                tenant_id=record.tenant_id,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                actor_id=record.actor_id,
                correlation_id=record.correlation_id,
                outcome=record.status.value,
                occurred_at=datetime.now(UTC),
                reason="Authoritative Agent run lifecycle transition",
                metadata={
                    "agent_id": record.agent_id,
                    "agent_version": record.agent_version,
                    "registry_digest": record.registry_digest,
                },
            )
        )

    @staticmethod
    def _copy(record: AuthoritativeRunRecord) -> AuthoritativeRunRecord:
        return replace(
            record,
            request=copy.deepcopy(record.request),
            result=copy.deepcopy(record.result),
        )
