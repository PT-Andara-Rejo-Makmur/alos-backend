"""Service layer for authorized memory access and evidence bundles."""

from __future__ import annotations

from alos.context.policy import maximum_classification
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
        if any(
            getattr(record, key) != getattr(principal, key)
            for key in ("tenant_id", "organization_id", "workspace_id", "actor_id")
        ):
            raise MemoryRepositoryError("memory is outside the authorized boundary")
        return self._repository.build_evidence_bundle(
            memory_id=memory_id,
            run_id=run_id,
            correlation_id=correlation_id,
        )

    def retrieve_conversation(self, *, principal: Principal, thread_id: str) -> list[MemoryRecord]:
        """Only explicitly governed, evidence-backed actor/thread memory enters ARA context."""
        ranks = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}
        return [
            record
            for record in self.retrieve(principal=principal)
            if record.actor_id == principal.actor_id
            and record.metadata.get("thread_id") == thread_id
            and ranks.get(record.classification, 99)
            <= ranks[maximum_classification(principal)]
            and record.source_ref
            and record.evidence_ref
            and record.run_id
            and record.correlation_id
        ]

    def expire(self, *, memory_id: str, reason: str = "Expiry policy") -> MemoryRecord:
        return self._repository.expire(memory_id=memory_id, reason=reason)
