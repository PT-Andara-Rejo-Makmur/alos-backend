"""Backend-owned authoritative Agent run lifecycle and audit boundary."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from alos.audit import AuditEvent, AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import RegistryEntry, RegistryNotFoundError, RegistryState
from alos.skills.registry import SkillRegistry

AGENT_RUN_REQUEST_SCHEMA = "https://schemas.alos.dev/v1/agent/agent-run-request.schema.json"
AGENT_RUN_RESULT_SCHEMA = "https://schemas.alos.dev/v1/agent/agent-run-result.schema.json"


class RunAuthorityError(ValueError):
    def __init__(self, message: str, *, code: str = "RUN_NOT_AUTHORIZED") -> None:
        super().__init__(message)
        self.code = code


class AuthoritativeRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    NEEDS_INFO = "NEEDS_INFO"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def is_terminal(self) -> bool:
        return self in {self.CANCELLED, self.BLOCKED, self.COMPLETED, self.FAILED, self.TIMED_OUT}


class AuthoritativeStepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    NEEDS_INFO = "NEEDS_INFO"


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
    authorized_skill_refs: tuple[tuple[str, str], ...]
    request: dict[str, Any]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancellation_state: str = "NONE"
    evidence_refs: tuple[str, ...] = ()
    usage_ref: str | None = None
    cost_ref: str | None = None
    structured_result_ref: str | None = None
    run_summary: dict[str, Any] = field(default_factory=dict)
    step_count: int = 0
    model_provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    model_cost: float | None = None
    tool_cost: float | None = None
    total_cost: float | None = None
    budget_limit: float | None = None
    remaining_budget: float | None = None


@dataclass(frozen=True, slots=True)
class AuthoritativeStepRecord:
    step_id: str
    run_id: str
    sequence: int
    step_type: str
    status: AuthoritativeStepStatus
    correlation_id: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tool_id: str | None = None
    input_metadata: dict[str, Any] = field(default_factory=dict)
    output_metadata: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    evidence_refs: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    tool_cost: float | None = None


class AgentRunStore(Protocol):
    async def create(self, record: AuthoritativeRunRecord) -> None: ...

    async def update(self, record: AuthoritativeRunRecord) -> None: ...

    async def get(self, run_id: str) -> AuthoritativeRunRecord: ...


class RunStepStore(Protocol):
    async def create(self, record: AuthoritativeStepRecord) -> None: ...

    async def update(self, record: AuthoritativeStepRecord) -> None: ...

    async def get(self, step_id: str) -> AuthoritativeStepRecord: ...

    async def list_for_run(self, run_id: str) -> list[AuthoritativeStepRecord]: ...


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


class InMemoryRunStepStore:
    def __init__(self) -> None:
        self._steps: dict[str, AuthoritativeStepRecord] = {}
        self._by_run: dict[str, list[AuthoritativeStepRecord]] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: AuthoritativeStepRecord) -> None:
        async with self._lock:
            if record.step_id in self._steps:
                raise RunAuthorityError("step_id already exists")
            self._steps[record.step_id] = record
            self._by_run.setdefault(record.run_id, []).append(record)

    async def update(self, record: AuthoritativeStepRecord) -> None:
        async with self._lock:
            if record.step_id not in self._steps:
                raise RunAuthorityError("authoritative step was not found")
            self._steps[record.step_id] = record
            existing = self._by_run.setdefault(record.run_id, [])
            for index, item in enumerate(existing):
                if item.step_id == record.step_id:
                    existing[index] = record
                    return
            existing.append(record)

    async def get(self, step_id: str) -> AuthoritativeStepRecord:
        async with self._lock:
            record = self._steps.get(step_id)
            if record is None:
                raise RunAuthorityError("authoritative step was not found")
            return record

    async def list_for_run(self, run_id: str) -> list[AuthoritativeStepRecord]:
        async with self._lock:
            return list(self._by_run.get(run_id, []))


class AgentRunAuthority:
    """Validate lifecycle/context and retain canonical run state in Backend."""

    def __init__(
        self,
        *,
        contracts: CanonicalContractCatalog,
        audit: AuditSink,
        skills: SkillRegistry | None = None,
        allow_test_drafts: bool = False,
        store: AgentRunStore | None = None,
        step_store: RunStepStore | None = None,
    ) -> None:
        self._contracts = contracts
        self._audit = audit
        self._skills = skills
        self._allow_test_drafts = allow_test_drafts
        self._store = store or InMemoryAgentRunStore()
        self._step_store = step_store or InMemoryRunStepStore()

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
        authorized_skill_refs = self._authorize_skills(request, context, agent)
        request["authorized_skill_refs"] = [
            {"skill_id": skill_id, "skill_version": version}
            for skill_id, version in authorized_skill_refs
        ]
        run_id = str(request["run_id"])
        execution_budget = context.get("execution_budget") if isinstance(context.get("execution_budget"), dict) else {}
        budget_limit = None
        if isinstance(execution_budget, dict):
            raw_limit = execution_budget.get("max_cost")
            if raw_limit is not None:
                budget_limit = float(raw_limit)
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
            authorized_skill_refs=authorized_skill_refs,
            request=copy.deepcopy(request),
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            cancellation_state="NONE",
            budget_limit=budget_limit,
            remaining_budget=budget_limit,
        )
        await self._store.create(record)
        await self._record_event(record, "run.started")
        return self._copy(record)

    async def complete(self, payload: dict[str, Any]) -> AuthoritativeRunRecord:
        normalized = self._normalize_result_payload(payload)
        result = self._contracts.validate(AGENT_RUN_RESULT_SCHEMA, normalized)
        run_id = str(result["run_id"])
        current = await self._store.get(run_id)
        if current.status.is_terminal:
            raise RunAuthorityError("authoritative run is already terminal")
        self._require_result_matches(current, result)
        try:
            target = AuthoritativeRunStatus(str(result["status"]))
        except ValueError as exc:
            raise RunAuthorityError("AgentRunResult is not terminal") from exc
        if target is AuthoritativeRunStatus.RUNNING:
            raise RunAuthorityError("AgentRunResult is not terminal")

        usage = result.get("usage")
        if isinstance(usage, dict):
            current = self._apply_usage_metrics(current, usage)
        tool_results = result.get("tool_results", [])
        if isinstance(tool_results, list):
            tool_cost = self._sum_tool_costs(tool_results)
            if tool_cost is not None:
                current = replace(
                    current,
                    tool_cost=tool_cost,
                )
        total_cost = self._compute_total_cost(current, usage)
        if total_cost is not None:
            current = replace(current, total_cost=total_cost)

        updated = replace(
            current,
            status=target,
            completed_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            result=copy.deepcopy(result),
            error_code=result.get("error", {}).get("code") if isinstance(result.get("error"), dict) else None,
            error_message=result.get("error", {}).get("message") if isinstance(result.get("error"), dict) else None,
            structured_result_ref=result.get("structured_result_ref"),
            evidence_refs=tuple(str(item) for item in result.get("evidence_refs", [])),
            cancellation_state="NONE" if target is not AuthoritativeRunStatus.CANCELLED else "CANCELLED",
        )
        updated = self._apply_budget(updated)
        if updated.status is AuthoritativeRunStatus.FAILED and updated.error_code == "BUDGET_EXCEEDED":
            await self._store.update(updated)
            await self._record_event(updated, "run.budget_exceeded")
            raise RunAuthorityError("BUDGET_EXCEEDED: authoritative run exceeded its execution budget", code="BUDGET_EXCEEDED")
        await self._store.update(updated)
        event_type = "run.completed" if target is AuthoritativeRunStatus.COMPLETED else "run.failed"
        await self._record_event(updated, event_type)
        return self._copy(updated)

    async def cancel(
        self,
        run_id: str,
        *,
        actor_id: str | None = None,
        reason: str = "Cancellation requested by backend authority.",
        correlation_id: str | None = None,
    ) -> AuthoritativeRunRecord:
        current = await self._store.get(run_id)
        if current.status.is_terminal:
            return self._copy(current)
        updated = replace(
            current,
            status=AuthoritativeRunStatus.CANCELLED,
            cancellation_state="CANCELLED",
            finished_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            error_code="RUN_CANCELLED",
            error_message=reason if reason and "\n" not in reason else reason.splitlines()[0],
            result={
                **(copy.deepcopy(current.result) or {}),
                "status": AuthoritativeRunStatus.CANCELLED.value,
                "cancelled_by": actor_id or current.actor_id,
                "cancel_reason": reason if reason and "\n" not in reason else reason.splitlines()[0],
                "correlation_id": correlation_id or current.correlation_id,
            },
        )
        await self._store.update(updated)
        await self._record_event(updated, "run.cancelled")
        return self._copy(updated)

    async def request_cancel(
        self,
        run_id: str,
        *,
        actor_id: str | None = None,
        reason: str = "Cancellation requested by backend authority.",
        correlation_id: str | None = None,
    ) -> AuthoritativeRunRecord:
        current = await self._store.get(run_id)
        if current.status is AuthoritativeRunStatus.CANCELLED:
            return self._copy(current)
        if current.status is AuthoritativeRunStatus.CANCEL_REQUESTED:
            return self._copy(current)
        if current.status.is_terminal:
            if current.status is AuthoritativeRunStatus.FAILED and current.error_code == "BUDGET_EXCEEDED":
                raise RunAuthorityError("BUDGET_EXCEEDED: authoritative run exceeded its execution budget", code="BUDGET_EXCEEDED")
            return self._copy(current)
        updated = replace(
            current,
            status=AuthoritativeRunStatus.CANCEL_REQUESTED,
            cancellation_state="REQUESTED",
            error_code="RUN_CANCEL_REQUESTED",
            error_message=reason if reason and "\n" not in reason else reason.splitlines()[0],
            result={
                **(copy.deepcopy(current.result) or {}),
                "status": AuthoritativeRunStatus.CANCEL_REQUESTED.value,
                "cancelled_by": actor_id or current.actor_id,
                "cancel_reason": reason if reason and "\n" not in reason else reason.splitlines()[0],
                "correlation_id": correlation_id or current.correlation_id,
            },
        )
        await self._store.update(updated)
        await self._record_event(updated, "run.cancel_requested")
        return self._copy(updated)

    async def begin_step(
        self,
        run_id: str,
        *,
        step_type: str = "tool",
        tool_id: str | None = None,
        sequence: int | None = None,
        input_metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AuthoritativeStepRecord:
        record = await self.get(run_id)
        if record.status.is_terminal or record.status is AuthoritativeRunStatus.CANCEL_REQUESTED:
            raise RunAuthorityError("cannot start a step on a terminal or cancelling run")
        next_sequence = int(sequence) if sequence is not None else record.step_count + 1
        step_id = f"{run_id}.step.{next_sequence}"
        step = AuthoritativeStepRecord(
            step_id=step_id,
            run_id=run_id,
            sequence=next_sequence,
            step_type=step_type,
            status=AuthoritativeStepStatus.RUNNING,
            correlation_id=str(correlation_id or record.correlation_id),
            started_at=datetime.now(UTC),
            tool_id=tool_id,
            input_metadata=copy.deepcopy(input_metadata or {}),
        )
        await self._step_store.create(step)
        updated_run = replace(
            record,
            step_count=max(record.step_count, next_sequence),
        )
        await self._store.update(updated_run)
        return step

    async def update_step_status(
        self,
        step_id: str,
        *,
        status: AuthoritativeStepStatus,
        output_metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        tool_cost: float | None = None,
    ) -> AuthoritativeStepRecord:
        step = await self._step_store.get(step_id)
        updated = replace(
            step,
            status=status,
            finished_at=datetime.now(UTC),
            output_metadata=copy.deepcopy(output_metadata or step.output_metadata),
            error_code=error_code,
            error_message=error_message if error_message is None or "\n" not in error_message else error_message.splitlines()[0],
            input_tokens=self._coerce_int(input_tokens, default=step.input_tokens),
            output_tokens=self._coerce_int(output_tokens, default=step.output_tokens),
            total_tokens=self._coerce_int(total_tokens, default=step.total_tokens),
            tool_cost=self._coerce_float(tool_cost, default=step.tool_cost),
        )
        await self._step_store.update(updated)
        run = await self.get(step.run_id)
        steps = await self._step_store.list_for_run(step.run_id)
        accumulated_input = self._sum_metric("input_tokens", steps)
        accumulated_output = self._sum_metric("output_tokens", steps)
        accumulated_total = self._sum_metric("total_tokens", steps)
        step_tool_cost = self._sum_metric("tool_cost", steps)
        run_updated = replace(
            run,
            input_tokens=accumulated_input,
            output_tokens=accumulated_output,
            total_tokens=accumulated_total,
            tool_cost=step_tool_cost,
        )
        if run_updated.budget_limit is not None and run_updated.total_cost is not None:
            run_updated = replace(run_updated, remaining_budget=run_updated.budget_limit - run_updated.total_cost)
        elif run_updated.budget_limit is not None and run_updated.tool_cost is not None:
            run_updated = replace(run_updated, remaining_budget=run_updated.budget_limit - (run_updated.tool_cost or 0.0))
        await self._store.update(run_updated)
        return updated

    async def get(self, run_id: str) -> AuthoritativeRunRecord:
        return await self._store.get(run_id)

    async def list_steps(self, run_id: str) -> list[AuthoritativeStepRecord]:
        return sorted(
            await self._step_store.list_for_run(run_id),
            key=lambda item: (item.sequence, item.started_at or datetime.min.replace(tzinfo=UTC)),
        )

    @staticmethod
    def _normalize_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        status = str(normalized.get("status") or "COMPLETED")
        if "output_state" not in normalized:
            normalized["output_state"] = (
                "AI_INFERRED"
                if status == "COMPLETED"
                else "BLOCKED" if status in {"FAILED", "CANCELLED", "TIMED_OUT"} else "DRAFT"
            )
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if "started_at" not in normalized:
            normalized["started_at"] = now
        if "completed_at" not in normalized:
            normalized["completed_at"] = now

        usage = normalized.get("usage")
        if isinstance(usage, dict):
            allowed_usage_keys = {"provider", "model", "input_tokens", "output_tokens", "latency_milliseconds", "estimated_cost"}
            normalized["usage"] = {
                key: value for key, value in usage.items() if key in allowed_usage_keys and value is not None
            }

        evidence_refs = normalized.get("evidence_refs", [])
        if isinstance(evidence_refs, list):
            normalized["evidence_refs"] = [
                AgentRunAuthority._normalize_evidence_ref(item, completed_at=normalized.get("completed_at") or now)
                for item in evidence_refs
            ]

        tool_results = normalized.get("tool_results", [])
        if isinstance(tool_results, list):
            normalized["tool_results"] = [
                AgentRunAuthority._normalize_tool_result(item, run_id=normalized.get("run_id"), correlation_id=normalized.get("correlation_id"), completed_at=normalized.get("completed_at") or now)
                for item in tool_results
            ]
        return normalized

    @staticmethod
    def _normalize_evidence_ref(item: Any, *, completed_at: str) -> dict[str, Any]:
        if isinstance(item, dict):
            normalized = dict(item)
        else:
            normalized = {
                "evidence_id": str(item),
                "source_id": "legacy-evidence",
                "uri": f"https://example.invalid/evidence/{str(item)}",
                "captured_at": completed_at,
                "content_hash": "sha256:" + ("0" * 64),
            }
        if "evidence_id" not in normalized:
            normalized["evidence_id"] = str(item)
        if "source_id" not in normalized:
            normalized["source_id"] = "legacy-evidence"
        if "uri" not in normalized:
            normalized["uri"] = f"https://example.invalid/evidence/{normalized['evidence_id']}"
        if "captured_at" not in normalized:
            normalized["captured_at"] = completed_at
        if "content_hash" not in normalized:
            normalized["content_hash"] = "sha256:" + ("0" * 64)
        return normalized

    @staticmethod
    def _normalize_tool_result(item: Any, *, run_id: Any, correlation_id: Any, completed_at: str) -> dict[str, Any]:
        if not isinstance(item, dict):
            item = {"tool_id": "legacy.tool", "status": "SUCCESS", "output": {"legacy": str(item)}}
        normalized = dict(item)
        raw_cost = normalized.pop("cost", None)
        raw_tool_cost = normalized.pop("tool_cost", None)
        if "tool_call_id" not in normalized:
            normalized["tool_call_id"] = f"{run_id or 'legacy_run'}.tool.{len(normalized)}"
        if "run_id" not in normalized:
            normalized["run_id"] = str(run_id) if run_id is not None else "legacy_run"
        if "correlation_id" not in normalized:
            normalized["correlation_id"] = str(correlation_id) if correlation_id is not None else "legacy_corr"
        if "status" not in normalized:
            normalized["status"] = "SUCCESS"
        if "completed_at" not in normalized:
            normalized["completed_at"] = completed_at
        if raw_cost is not None or raw_tool_cost is not None:
            value = raw_cost if raw_cost is not None else raw_tool_cost
            normalized["output"] = {"cost": float(value), **(dict(normalized.get("output")) if isinstance(normalized.get("output"), dict) else {})}
        if normalized.get("status") in {"SUCCESS", "COMPLETED"} and "output" not in normalized:
            normalized["output"] = {"legacy": "tool result normalized by backend authority"}
        if normalized.get("status") in {"FAILED", "TIMEOUT", "DENIED", "REJECTED"} and "error" not in normalized:
            normalized["error"] = {"code": "LEGACY_TOOL_RESULT", "message": "Tool result normalized by backend authority."}
        return normalized

    @staticmethod
    def _compute_total_cost(record: AuthoritativeRunRecord, usage: dict[str, Any] | None) -> float | None:
        model_cost = record.model_cost
        tool_cost = record.tool_cost
        if usage is not None:
            legacy_cost = usage.get("estimated_cost")
            if legacy_cost is not None:
                try:
                    model_cost = float(legacy_cost)
                except (TypeError, ValueError):
                    model_cost = record.model_cost
        if model_cost is None and tool_cost is None:
            return None
        return float((model_cost or 0.0) + (tool_cost or 0.0))

    @staticmethod
    def _coerce_int(value: int | None, *, default: int | None) -> int | None:
        if value is None:
            return default
        return int(value)

    @staticmethod
    def _coerce_float(value: float | None, *, default: float | None) -> float | None:
        if value is None:
            return default
        return float(value)

    @staticmethod
    def _sum_metric(name: str, records: list[AuthoritativeStepRecord]) -> int | None:
        values = [getattr(record, name) for record in records if getattr(record, name) is not None]
        if not values:
            return None
        return sum(int(value) for value in values)

    @staticmethod
    def _sum_tool_costs(tool_results: list[dict[str, Any]]) -> float | None:
        costs: list[float] = []
        for result in tool_results:
            if not isinstance(result, dict):
                continue
            value = result.get("cost")
            if value is None:
                value = result.get("tool_cost")
            if value is None and isinstance(result.get("output"), dict):
                value = result["output"].get("cost")
            if value is None and isinstance(result.get("output"), dict):
                value = result["output"].get("tool_cost")
            if value is None:
                continue
            try:
                costs.append(float(value))
            except (TypeError, ValueError):
                continue
        if not costs:
            return None
        return sum(costs)

    @classmethod
    def _apply_usage_metrics(cls, record: AuthoritativeRunRecord, usage: dict[str, Any]) -> AuthoritativeRunRecord:
        input_tokens = cls._coerce_int(usage.get("input_tokens"), default=record.input_tokens)
        output_tokens = cls._coerce_int(usage.get("output_tokens"), default=record.output_tokens)
        total_tokens = cls._coerce_int(usage.get("total_tokens"), default=record.total_tokens)
        if input_tokens is not None and output_tokens is not None and total_tokens is None:
            total_tokens = input_tokens + output_tokens
        cost_value = usage.get("estimated_cost")
        if cost_value is None:
            cost_value = usage.get("model_cost")
        if cost_value is None:
            cost_value = usage.get("cost")
        model_cost = cls._coerce_float(cost_value, default=record.model_cost)
        provider = usage.get("provider") or usage.get("model_provider") or record.model_provider
        return replace(
            record,
            model_provider=str(provider) if provider is not None else record.model_provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model_cost=model_cost,
        )

    @classmethod
    def _apply_budget(cls, record: AuthoritativeRunRecord) -> AuthoritativeRunRecord:
        if record.budget_limit is None:
            if record.total_cost is not None:
                return replace(record, remaining_budget=None)
            return record
        total_cost = record.total_cost if record.total_cost is not None else 0.0
        remaining = record.budget_limit - total_cost
        if remaining < 0:
            return replace(
                record,
                status=AuthoritativeRunStatus.FAILED,
                error_code="BUDGET_EXCEEDED",
                error_message="Execution budget exceeded.",
                remaining_budget=remaining,
            )
        return replace(record, remaining_budget=remaining)

    async def runtime_authorization(self, run_id: str) -> dict[str, Any]:
        record = await self.get(run_id)
        if record.status is not AuthoritativeRunStatus.RUNNING:
            raise RunAuthorityError("only a running record can be authorized")
        return {
            "run_id": record.run_id,
            "registry_digest": record.registry_digest,
            "lifecycle_state": record.lifecycle_authorization,
            "allowed_tool_ids": list(record.authorized_tool_ids),
            "authorized_skill_refs": [
                {"skill_id": skill_id, "skill_version": version}
                for skill_id, version in record.authorized_skill_refs
            ],
        }

    def _authorize_skills(
        self,
        request: dict[str, Any],
        context: dict[str, Any],
        agent: RegistryEntry,
    ) -> tuple[tuple[str, str], ...]:
        requested = request.get("authorized_skill_refs")
        raw_refs = agent.payload.get("skill_refs", []) if requested is None else requested
        agent_refs = {
            (str(item["skill_id"]), str(item["skill_version"]))
            for item in agent.payload.get("skill_refs", [])
        }
        effective_permissions = frozenset(context.get("permission_refs", [])) & frozenset(
            agent.payload.get("permission_refs", [])
        )
        effective_scopes = frozenset(context.get("scope_refs", [])) & frozenset(
            agent.payload.get("scope_refs", [])
        )
        effective_tools = frozenset(context.get("allowed_tool_ids", [])) & frozenset(
            agent.payload.get("tool_ids", [])
        )
        authorized: list[tuple[str, str]] = []
        for item in raw_refs:
            key = (str(item["skill_id"]), str(item["skill_version"]))
            if key not in agent_refs:
                raise RunAuthorityError(
                    "authorized_skill_refs contains a ref outside AgentDefinition.skill_refs",
                    code="SKILL_REF_NOT_ASSIGNED",
                )
            if self._skills is None:
                raise RunAuthorityError(
                    "Backend SkillRegistry is required to authorize Agent skills",
                    code="SKILL_REGISTRY_UNAVAILABLE",
                )
            try:
                skill = self._skills.get(
                    tenant_id=agent.tenant_id,
                    workspace_id=agent.workspace_id,
                    subject_id=key[0],
                    version=key[1],
                )
            except RegistryNotFoundError as exc:
                raise RunAuthorityError(
                    "exact SkillDefinition version was not found",
                    code="SKILL_VERSION_NOT_FOUND",
                ) from exc
            if skill.organization_id != agent.organization_id:
                raise RunAuthorityError(
                    "SkillDefinition organization does not match AgentDefinition",
                    code="SKILL_CONTEXT_MISMATCH",
                )
            if skill.state is not RegistryState.ACTIVE:
                raise RunAuthorityError(
                    "SkillDefinition exact version is not ACTIVE",
                    code="SKILL_NOT_ACTIVE",
                )
            skill_permissions = frozenset(skill.payload.get("permission_refs", []))
            if not skill_permissions.issubset(effective_permissions):
                raise RunAuthorityError(
                    "effective run authority lacks Skill permission prerequisites",
                    code="SKILL_PERMISSION_MISMATCH",
                )
            skill_scopes = frozenset(skill.payload.get("scope_refs", []))
            if not skill_scopes.issubset(effective_scopes):
                raise RunAuthorityError(
                    "effective run authority lacks Skill scope prerequisites",
                    code="SKILL_SCOPE_MISMATCH",
                )
            skill_tools = frozenset(skill.payload.get("required_tool_ids", []))
            if not skill_tools.issubset(effective_tools):
                raise RunAuthorityError(
                    "effective run authority lacks Skill tool prerequisites",
                    code="SKILL_TOOL_MISMATCH",
                )
            authorized.append(key)
        return tuple(sorted(set(authorized)))

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
                    "authorized_skill_refs": [
                        {"skill_id": skill_id, "skill_version": version}
                        for skill_id, version in record.authorized_skill_refs
                    ],
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
