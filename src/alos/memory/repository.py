"""Backend-owned memory repository with fail-closed retention and scope filtering."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any

from alos.audit import AuditEvent, AuditSink, InMemoryAuditRepository
from alos.identity import DataScope, Principal
from alos.memory.models import MemoryEvidenceBundle, MemoryRecord, MemoryStatus


class MemoryRepositoryError(ValueError):
    pass


class MemoryRepository:
    _CLASSIFICATION_RANK: dict[str, int] = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }

    def __init__(self, *, audit: AuditSink | None = None) -> None:
        self._store: dict[str, MemoryRecord] = {}
        self._audit = audit or InMemoryAuditRepository()

    def write(self, record: MemoryRecord) -> MemoryRecord:
        if not record.memory_id.strip():
            raise MemoryRepositoryError("memory_id must not be blank")
        if record.status == MemoryStatus.EXPIRED and record.expires_at is None:
            record.status = MemoryStatus.EXPIRED
        stored = record.model_copy(deep=True)
        self._store[stored.memory_id] = stored
        self._append_audit(
            event_type="memory.record.written",
            entity_id=stored.memory_id,
            actor_id=stored.actor_id,
            tenant_id=stored.tenant_id,
            organization_id=stored.organization_id,
            workspace_id=stored.workspace_id,
            correlation_id=stored.correlation_id or "unknown",
            outcome="SUCCESS",
            reason="Material memory record stored by backend authority.",
            metadata={
                "source_ref": stored.source_ref,
                "evidence_ref": stored.evidence_ref,
                "classification": stored.classification,
                "scope_refs": list(stored.scope_refs),
                "expires_at": stored.expires_at.isoformat() if stored.expires_at else None,
            },
        )
        return stored.model_copy(deep=True)

    def retrieve(
        self,
        *,
        principal: Principal,
        query: str | None = None,
        limit: int = 12,
    ) -> list[MemoryRecord]:
        if not principal.active:
            raise MemoryRepositoryError("principal is not active")
        now = datetime.now(UTC)
        matches: list[MemoryRecord] = []
        for record in self._store.values():
            if record.tenant_id != principal.tenant_id:
                continue
            if record.organization_id != principal.organization_id:
                continue
            if record.workspace_id != principal.workspace_id:
                continue
            if record.status is not MemoryStatus.ACTIVE:
                continue
            if record.expires_at is not None and now >= record.expires_at:
                continue
            if not self._scope_matches(record, principal):
                continue
            if not self._classification_allowed(record.classification, principal):
                continue
            if record.division_id and principal.division_id and record.division_id != principal.division_id:
                continue
            if record.project_id and principal.project_id and record.project_id != principal.project_id:
                continue
            if query and query.strip() and not self._query_matches(record, query):
                continue
            matches.append(record.model_copy(deep=True))
        matches.sort(key=lambda item: item.created_at, reverse=True)
        return matches[:limit]

    def get(self, *, memory_id: str) -> MemoryRecord:
        if memory_id not in self._store:
            raise MemoryRepositoryError(f"memory {memory_id} was not found")
        return self._store[memory_id].model_copy(deep=True)

    def expire(self, *, memory_id: str, reason: str = "Expiry policy") -> MemoryRecord:
        record = self.get(memory_id=memory_id)
        updated = record.model_copy(update={"status": MemoryStatus.EXPIRED})
        self._store[memory_id] = updated
        self._append_audit(
            event_type="memory.record.expired",
            entity_id=memory_id,
            actor_id=record.actor_id,
            tenant_id=record.tenant_id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            correlation_id=record.correlation_id or "unknown",
            outcome="EXPIRED",
            reason=reason,
            metadata={"expires_at": (datetime.now(UTC).isoformat())},
        )
        return updated.model_copy(deep=True)

    def delete(self, *, memory_id: str, reason: str = "Explicit delete") -> MemoryRecord:
        record = self.get(memory_id=memory_id)
        updated = record.model_copy(update={"status": MemoryStatus.DELETED})
        self._store[memory_id] = updated
        self._append_audit(
            event_type="memory.record.deleted",
            entity_id=memory_id,
            actor_id=record.actor_id,
            tenant_id=record.tenant_id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            correlation_id=record.correlation_id or "unknown",
            outcome="DELETED",
            reason=reason,
            metadata={"status": "DELETED"},
        )
        return updated.model_copy(deep=True)

    def build_evidence_bundle(
        self,
        *,
        memory_id: str,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MemoryEvidenceBundle:
        record = self.get(memory_id=memory_id)
        evidence_refs = []
        if record.evidence_ref:
            evidence_refs.append(record.evidence_ref)
        source_refs = []
        if record.source_ref:
            source_refs.append(record.source_ref)
        return MemoryEvidenceBundle(
            memory_refs=[record.memory_id],
            source_refs=source_refs,
            evidence_refs=evidence_refs,
            run_id=run_id or record.run_id,
            correlation_id=correlation_id or record.correlation_id,
            classification=record.classification,
            freshness="CURRENT" if record.expires_at is None or record.expires_at > datetime.now(UTC) else "STALE",
        )

    @staticmethod
    def _scope_matches(record: MemoryRecord, principal: Principal) -> bool:
        required_scopes = set(record.scope_refs)
        if not required_scopes:
            return False
        return required_scopes.issubset(principal.scopes)

    @staticmethod
    def _query_matches(record: MemoryRecord, query: str) -> bool:
        needle = query.casefold()
        haystack = " ".join(
            [
                record.content,
                record.source_ref or "",
                record.evidence_ref or "",
                record.memory_id,
            ]
        ).casefold()
        return needle in haystack

    @staticmethod
    def _classification_allowed(classification: str, principal: Principal) -> bool:
        if principal.data_scope is DataScope.OWN_ASSIGNED:
            return classification in {"PUBLIC", "INTERNAL"}
        rank = MemoryRepository._CLASSIFICATION_RANK.get(classification, 99)
        if principal.project_id:
            return rank <= MemoryRepository._CLASSIFICATION_RANK.get("INTERNAL", 1)
        return rank <= MemoryRepository._CLASSIFICATION_RANK.get("CONFIDENTIAL", 2)

    def _append_audit(
        self,
        *,
        event_type: str,
        entity_id: str,
        actor_id: str,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        correlation_id: str,
        outcome: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            entity_type="memory",
            entity_id=entity_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            reason=reason,
            metadata=metadata,
        )
        result = self._audit.append(event)
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                asyncio.create_task(result)
