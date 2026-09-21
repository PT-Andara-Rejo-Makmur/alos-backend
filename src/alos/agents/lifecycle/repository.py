"""SQLAlchemy persistence adapter for authoritative Agent run lifecycle."""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.agents.lifecycle.runs import (
    AuthoritativeRunRecord,
    AuthoritativeRunStatus,
    RunAuthorityError,
)
from alos.persistence.models import AgentRunRecord


class SqlAgentRunStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, record: AuthoritativeRunRecord) -> None:
        async with self._session_factory() as session:
            if await session.get(AgentRunRecord, record.run_id) is not None:
                raise RunAuthorityError("run_id already exists")
            session.add(self._to_row(record))
            await session.commit()

    async def update(self, record: AuthoritativeRunRecord) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentRunRecord, record.run_id)
            if row is None:
                raise RunAuthorityError("authoritative run was not found")
            row.status = record.status.value
            row.result_payload = cast(dict[str, object] | None, record.result)
            row.completed_at = record.completed_at
            await session.commit()

    async def get(self, run_id: str) -> AuthoritativeRunRecord:
        async with self._session_factory() as session:
            row = await session.get(AgentRunRecord, run_id)
            if row is None:
                raise RunAuthorityError("authoritative run was not found")
            return self._from_row(row)

    @staticmethod
    def _to_row(record: AuthoritativeRunRecord) -> AgentRunRecord:
        parent_run_id = record.request.get("parent_run_id")
        return AgentRunRecord(
            run_id=record.run_id,
            root_run_id=record.root_run_id,
            parent_run_id=str(parent_run_id) if parent_run_id is not None else None,
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
            completed_at=record.completed_at,
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
            completed_at=row.completed_at,
            result=cast(dict[str, Any] | None, row.result_payload),
        )
