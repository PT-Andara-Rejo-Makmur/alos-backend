"""SQLAlchemy persistence adapter for authoritative Agent run lifecycle."""

import inspect
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.agents.lifecycle.runs import (
    AuthoritativeRunRecord,
    AuthoritativeRunStatus,
    AuthoritativeStepRecord,
    RunAuthorityError,
)
from alos.persistence.models import AgentRunRecord, AgentRunStepRecord


def _uses_async_context(session: Any) -> bool:
    return hasattr(session, "__aenter__") and callable(getattr(session, "__aenter__", None))


def _call_get(session: Any, key: Any, value: Any) -> Any:
    getter = getattr(session, "get", None)
    if inspect.iscoroutinefunction(getter):
        return getter(key, value)
    return getter(key, value)


class SqlAgentRunStepStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, record: AuthoritativeStepRecord) -> None:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                existing = s.get(AgentRunStepRecord, record.step_id)
                if existing is not None:
                    raise RunAuthorityError("step_id already exists")
                s.add(self._to_row(record))
                s.commit()
            return
        with session as s:
            if s.get(AgentRunStepRecord, record.step_id) is not None:
                raise RunAuthorityError("step_id already exists")
            s.add(self._to_row(record))
            s.commit()

    async def update(self, record: AuthoritativeStepRecord) -> None:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                row = s.get(AgentRunStepRecord, record.step_id)
                if row is None:
                    raise RunAuthorityError("authoritative step was not found")
                row.status = record.status.value
                row.finished_at = record.finished_at
                row.output_metadata = cast(dict[str, object], record.output_metadata)
                row.error_code = record.error_code
                row.error_message = record.error_message
                row.evidence_refs = list(record.evidence_refs)
                row.tool_cost = record.tool_cost
                s.commit()
            return
        with session as s:
            row = s.get(AgentRunStepRecord, record.step_id)
            if row is None:
                raise RunAuthorityError("authoritative step was not found")
            row.status = record.status.value
            row.finished_at = record.finished_at
            row.output_metadata = cast(dict[str, object], record.output_metadata)
            row.error_code = record.error_code
            row.error_message = record.error_message
            row.evidence_refs = list(record.evidence_refs)
            row.tool_cost = record.tool_cost
            s.commit()

    async def get(self, step_id: str) -> AuthoritativeStepRecord:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                row = s.get(AgentRunStepRecord, step_id)
                if row is None:
                    raise RunAuthorityError("authoritative step was not found")
                return self._from_row(row)
        with session as s:
            row = s.get(AgentRunStepRecord, step_id)
            if row is None:
                raise RunAuthorityError("authoritative step was not found")
            return self._from_row(row)

    async def list_for_run(self, run_id: str) -> list[AuthoritativeStepRecord]:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                rows = s.execute(__import__("sqlalchemy").select(AgentRunStepRecord).where(AgentRunStepRecord.run_id == run_id)).scalars().all()
                return [self._from_row(row) for row in rows]
        with session as s:
            rows = s.execute(__import__("sqlalchemy").select(AgentRunStepRecord).where(AgentRunStepRecord.run_id == run_id)).scalars().all()
            return [self._from_row(row) for row in rows]

    @staticmethod
    def _to_row(record: AuthoritativeStepRecord) -> AgentRunStepRecord:
        return AgentRunStepRecord(
            step_id=record.step_id,
            run_id=record.run_id,
            sequence=record.sequence,
            step_type=record.step_type,
            status=record.status.value,
            correlation_id=record.correlation_id,
            started_at=record.started_at,
            finished_at=record.finished_at,
            tool_id=record.tool_id,
            input_metadata=cast(dict[str, object], record.input_metadata),
            output_metadata=cast(dict[str, object], record.output_metadata),
            error_code=record.error_code,
            error_message=record.error_message,
            evidence_refs=list(record.evidence_refs),
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            tool_cost=record.tool_cost,
        )

    @staticmethod
    def _from_row(row: AgentRunStepRecord) -> AuthoritativeStepRecord:
        return AuthoritativeStepRecord(
            step_id=row.step_id,
            run_id=row.run_id,
            sequence=row.sequence,
            step_type=row.step_type,
            status=AuthoritativeRunStatus.__members__.get(row.status, "") if False else __import__("alos.agents.lifecycle.runs", fromlist=["AuthoritativeStepStatus"]).AuthoritativeStepStatus(row.status),
            correlation_id=row.correlation_id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            tool_id=row.tool_id,
            input_metadata=cast(dict[str, Any], row.input_metadata),
            output_metadata=cast(dict[str, Any], row.output_metadata),
            error_code=row.error_code,
            error_message=row.error_message,
            evidence_refs=tuple(row.evidence_refs or []),
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.total_tokens,
            tool_cost=row.tool_cost,
        )


class SqlAgentRunStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, record: AuthoritativeRunRecord) -> None:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                existing = s.get(AgentRunRecord, record.run_id)
                if existing is not None:
                    raise RunAuthorityError("run_id already exists")
                s.add(self._to_row(record))
                s.commit()
            return
        with session as s:
            if s.get(AgentRunRecord, record.run_id) is not None:
                raise RunAuthorityError("run_id already exists")
            s.add(self._to_row(record))
            s.commit()

    async def update(self, record: AuthoritativeRunRecord) -> None:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                row = s.get(AgentRunRecord, record.run_id)
                if row is None:
                    raise RunAuthorityError("authoritative run was not found")
                row.status = record.status.value
                row.result_payload = cast(dict[str, object] | None, record.result)
                row.completed_at = record.completed_at
                row.finished_at = record.finished_at
                row.error_code = record.error_code
                row.error_message = record.error_message
                row.cancellation_state = record.cancellation_state
                row.evidence_refs = list(record.evidence_refs)
                row.usage_ref = record.usage_ref
                row.cost_ref = record.cost_ref
                row.structured_result_ref = record.structured_result_ref
                row.model_provider = record.model_provider
                row.input_tokens = record.input_tokens
                row.output_tokens = record.output_tokens
                row.total_tokens = record.total_tokens
                row.model_cost = record.model_cost
                row.tool_cost = record.tool_cost
                row.total_cost = record.total_cost
                row.budget_limit = record.budget_limit
                row.remaining_budget = record.remaining_budget
                s.commit()
            return
        with session as s:
            row = s.get(AgentRunRecord, record.run_id)
            if row is None:
                raise RunAuthorityError("authoritative run was not found")
            row.status = record.status.value
            row.result_payload = cast(dict[str, object] | None, record.result)
            row.completed_at = record.completed_at
            row.finished_at = record.finished_at
            row.error_code = record.error_code
            row.error_message = record.error_message
            row.cancellation_state = record.cancellation_state
            row.evidence_refs = list(record.evidence_refs)
            row.usage_ref = record.usage_ref
            row.cost_ref = record.cost_ref
            row.structured_result_ref = record.structured_result_ref
            row.model_provider = record.model_provider
            row.input_tokens = record.input_tokens
            row.output_tokens = record.output_tokens
            row.total_tokens = record.total_tokens
            row.model_cost = record.model_cost
            row.tool_cost = record.tool_cost
            row.total_cost = record.total_cost
            row.budget_limit = record.budget_limit
            row.remaining_budget = record.remaining_budget
            s.commit()

    async def get(self, run_id: str) -> AuthoritativeRunRecord:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                row = s.get(AgentRunRecord, run_id)
                if row is None:
                    raise RunAuthorityError("authoritative run was not found")
                return self._from_row(row)
        with session as s:
            row = s.get(AgentRunRecord, run_id)
            if row is None:
                raise RunAuthorityError("authoritative run was not found")
            return self._from_row(row)

    async def list(self) -> list[AuthoritativeRunRecord]:
        session = self._session_factory()
        if _uses_async_context(session):
            async with session as s:
                rows = s.execute(__import__("sqlalchemy").select(AgentRunRecord)).scalars().all()
                return [self._from_row(row) for row in rows]
        with session as s:
            rows = s.execute(__import__("sqlalchemy").select(AgentRunRecord)).scalars().all()
            return [self._from_row(row) for row in rows]

    @staticmethod
    def _to_row(record: AuthoritativeRunRecord) -> AgentRunRecord:
        return AgentRunRecord(
            run_id=record.run_id,
            root_run_id=record.root_run_id,
            parent_run_id=record.parent_run_id,
            delegation_id=record.delegation_id,
            depth=record.depth,
            retry_count=record.retry_count,
            tenant_id=record.tenant_id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            actor_id=record.actor_id,
            correlation_id=record.correlation_id,
            agent_id=record.agent_id,
            agent_version=record.agent_version,
            capability_id=record.capability_id,
            status=record.status.value,
            registry_digest=record.registry_digest,
            lifecycle_authorization=record.lifecycle_authorization,
            authorized_tool_ids=list(record.authorized_tool_ids),
            request_payload=cast(dict[str, object], record.request),
            result_payload=cast(dict[str, object] | None, record.result),
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            finished_at=record.finished_at,
            error_code=record.error_code,
            error_message=record.error_message,
            cancellation_state=record.cancellation_state,
            evidence_refs=list(record.evidence_refs),
            usage_ref=record.usage_ref,
            cost_ref=record.cost_ref,
            structured_result_ref=record.structured_result_ref,
            model_provider=record.model_provider,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            model_cost=record.model_cost,
            tool_cost=record.tool_cost,
            total_cost=record.total_cost,
            budget_limit=record.budget_limit,
            remaining_budget=record.remaining_budget,
        )

    @staticmethod
    def _from_row(row: AgentRunRecord) -> AuthoritativeRunRecord:
        request = cast(dict[str, Any], row.request_payload)
        authorized_skill_refs = tuple(
            (str(item["skill_id"]), str(item["skill_version"]))
            for item in request.get("authorized_skill_refs", [])
        )
        return AuthoritativeRunRecord(
            run_id=row.run_id,
            root_run_id=row.root_run_id,
            tenant_id=row.tenant_id,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            actor_id=row.actor_id,
            correlation_id=row.correlation_id,
            agent_id=row.agent_id,
            agent_version=row.agent_version,
            capability_id=row.capability_id,
            status=AuthoritativeRunStatus(row.status),
            registry_digest=row.registry_digest,
            lifecycle_authorization=row.lifecycle_authorization,
            authorized_tool_ids=tuple(row.authorized_tool_ids),
            authorized_skill_refs=authorized_skill_refs,
            request=request,
            created_at=row.created_at,
            parent_run_id=row.parent_run_id,
            depth=row.depth,
            delegation_id=row.delegation_id,
            retry_count=row.retry_count,
            started_at=row.started_at,
            completed_at=row.completed_at,
            finished_at=row.finished_at,
            result=cast(dict[str, Any] | None, row.result_payload),
            error_code=row.error_code,
            error_message=row.error_message,
            cancellation_state=row.cancellation_state,
            evidence_refs=tuple(row.evidence_refs or []),
            usage_ref=row.usage_ref,
            cost_ref=row.cost_ref,
            structured_result_ref=row.structured_result_ref,
            model_provider=row.model_provider,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.total_tokens,
            model_cost=row.model_cost,
            tool_cost=row.tool_cost,
            total_cost=row.total_cost,
            budget_limit=row.budget_limit,
            remaining_budget=row.remaining_budget,
        )
