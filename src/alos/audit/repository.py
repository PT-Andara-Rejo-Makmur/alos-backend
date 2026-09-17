from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.persistence.models import AuditRecord
from alos.tools.executor.service import ToolAuditRecord


class SqlToolAuditSink:
    """Insert-only audit sink. This interface intentionally exposes no update/delete."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, record: ToolAuditRecord) -> None:
        async with self._session_factory() as session:
            session.add(
                AuditRecord(
                    event_type="tool.execution",
                    entity_id=record.tool_call_id,
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    actor_id=record.actor_id,
                    correlation_id=record.correlation_id,
                    outcome=record.outcome,
                    occurred_at=record.occurred_at,
                )
            )
            await session.commit()
