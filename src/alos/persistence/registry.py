"""SQL persistence adapter for immutable registry definitions."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.persistence.models import RegistryDefinitionRecord
from alos.registry import RegistryEntry, RegistryState


class SqlRegistryStore:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, entry: RegistryEntry) -> None:
        async with self._session_factory() as session:
            statement = select(RegistryDefinitionRecord).where(
                RegistryDefinitionRecord.tenant_id == entry.tenant_id,
                RegistryDefinitionRecord.workspace_id == entry.workspace_id,
                RegistryDefinitionRecord.subject_type == entry.subject_type,
                RegistryDefinitionRecord.subject_id == entry.subject_id,
                RegistryDefinitionRecord.version == entry.version,
            )
            record = (await session.execute(statement)).scalar_one_or_none()
            if record is None:
                record = RegistryDefinitionRecord(
                    tenant_id=entry.tenant_id,
                    organization_id=entry.organization_id,
                    workspace_id=entry.workspace_id,
                    subject_type=entry.subject_type,
                    subject_id=entry.subject_id,
                    version=entry.version,
                    contract_payload=entry.payload,
                    digest=entry.digest,
                    lifecycle_state=entry.state.value,
                    created_by=entry.created_by,
                    correlation_id=entry.correlation_id,
                    decision_id=entry.decision_id,
                    release_id=entry.release_id,
                    created_at=entry.created_at,
                )
                session.add(record)
            else:
                record.lifecycle_state = entry.state.value
                record.correlation_id = entry.correlation_id
                record.decision_id = entry.decision_id
                record.release_id = entry.release_id
            await session.commit()

    async def load_subject_type(self, subject_type: str) -> tuple[RegistryEntry, ...]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(RegistryDefinitionRecord).where(
                            RegistryDefinitionRecord.subject_type == subject_type
                        )
                    )
                )
                .scalars()
                .all()
            )
        return tuple(self.entry_from_record(row) for row in rows)

    @staticmethod
    def entry_from_record(row: RegistryDefinitionRecord) -> RegistryEntry:
        return RegistryEntry(
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            version=row.version,
            tenant_id=row.tenant_id,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            payload=dict(row.contract_payload),
            digest=row.digest,
            state=RegistryState(row.lifecycle_state),
            created_by=row.created_by,
            correlation_id=row.correlation_id,
            created_at=row.created_at,
            decision_id=row.decision_id,
            release_id=row.release_id,
        )
