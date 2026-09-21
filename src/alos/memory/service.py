"""Service layer for authorized memory access and evidence bundles."""

from __future__ import annotations

from datetime import datetime

from alos.identity import Principal
from alos.memory.models import MemoryEvidenceBundle, MemoryRecord
from alos.memory.repository import MemoryRepository, MemoryRepositoryError


class MemoryService:
    def __init__(self, *, repository: MemoryRepository | None = None) -> None:
        self._repository = repository or MemoryRepository()

    def write(self, *, record: MemoryRecord) -> MemoryRecord:
        return self._repository.write(record)

    def retrieve(
        self,
        *,
        principal: Principal,
        query: str | None = None,
        limit: int = 12,
    ) -> list[MemoryRecord]:
        return self._repository.retrieve(principal=principal, query=query, limit=limit)

    def build_evidence_bundle(
        self,
        *,
        memory_id: str,
        principal: Principal,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MemoryEvidenceBundle:
        record = self._repository.get(memory_id=memory_id)
        if record.tenant_id != principal.tenant_id or record.workspace_id != principal.workspace_id:
            raise MemoryRepositoryError("memory is outside the authorized boundary")
        return self._repository.build_evidence_bundle(
            memory_id=memory_id,
            run_id=run_id,
            correlation_id=correlation_id,
        )

    def expire(self, *, memory_id: str, reason: str = "Expiry policy") -> MemoryRecord:
        return self._repository.expire(memory_id=memory_id, reason=reason)
