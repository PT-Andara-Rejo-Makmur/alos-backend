from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.audit.models import AuditEvent
from alos.persistence.models import AuditRecord

if TYPE_CHECKING:
    from alos.tools.executor.service import ToolAuditRecord


class AuditSink(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


class InMemoryAuditRepository:
    """Deterministic append-only audit repository for tests and local composition."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list_events(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        if not 1 <= limit <= 500:
            raise ValueError("audit listing limit must be between 1 and 500")
        matching = [event for event in self._events if event.tenant_id == tenant_id]
        if workspace_id is not None:
            matching = [event for event in matching if event.workspace_id == workspace_id]
        return tuple(reversed(matching[-limit:]))


class SqlAuditRepository:
    """Insert-only audit sink. This interface intentionally exposes no update/delete."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, event: AuditEvent) -> None:
        async with self._session_factory() as session:
            session.add(
                AuditRecord(
                    event_type=event.event_type,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    tenant_id=event.tenant_id,
                    organization_id=event.organization_id,
                    workspace_id=event.workspace_id,
                    actor_id=event.actor_id,
                    actor_kind=event.actor_kind,
                    correlation_id=event.correlation_id,
                    outcome=event.outcome,
                    reason=event.reason,
                    event_metadata=event.metadata,
                    occurred_at=event.occurred_at,
                )
            )
            await session.commit()

    async def list_events(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("audit listing limit must be between 1 and 500")
        query = select(AuditRecord).where(AuditRecord.tenant_id == tenant_id)
        if workspace_id is not None:
            query = query.where(AuditRecord.workspace_id == workspace_id)
        query = query.order_by(AuditRecord.occurred_at.desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.scalars(query)
            return tuple(result.all())


class SqlToolAuditSink:
    """Compatibility adapter from the ToolExecutor audit protocol to generic audit."""

    def __init__(self, repository: SqlAuditRepository) -> None:
        self._repository = repository

    async def append(self, record: ToolAuditRecord) -> None:
        await self._repository.append(
            AuditEvent(
                event_type="tool.execution",
                entity_type="tool_call",
                entity_id=record.tool_call_id,
                tenant_id=record.tenant_id,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                actor_id=record.actor_id,
                correlation_id=record.correlation_id,
                outcome=record.outcome,
                occurred_at=record.occurred_at,
                reason=f"Tool execution {record.outcome.lower()}",
                metadata={"tool_id": record.tool_id},
            )
        )
