"""Tenant-scoped immutable Source Registry adapted from MVP-1."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from alos.audit import AuditEvent, AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.evidence import EvidenceRegistry
from alos.sources.models import (
    KnowledgeAccessContext,
    SourceRegistration,
    SourceStatus,
    SourceVersionRecord,
)

CONTEXT_BUNDLE_SCHEMA = "https://schemas.alos.dev/v1/context/context-bundle.schema.json"


class SourceRegistryError(ValueError):
    pass


class SourceRegistry:
    _CLASSIFICATION_RANK: ClassVar[dict[str, int]] = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }

    def __init__(
        self,
        *,
        contracts: CanonicalContractCatalog,
        evidence: EvidenceRegistry,
        audit: AuditSink,
    ) -> None:
        self._contracts = contracts
        self._evidence = evidence
        self._audit = audit
        self._versions: dict[tuple[str, str, str, str], SourceVersionRecord] = {}
        self._classifications: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        command: SourceRegistration,
        *,
        access: KnowledgeAccessContext,
    ) -> SourceVersionRecord:
        self._require_scope(access, "scope.sources.write")
        if not command.content.strip():
            raise SourceRegistryError("source content must not be blank")
        source_key = (access.tenant_id, access.workspace_id, command.source_id)
        version_key = (*source_key, command.source_version)
        digest = f"sha256:{hashlib.sha256(command.content.encode('utf-8')).hexdigest()}"
        record = SourceVersionRecord(
            tenant_id=access.tenant_id,
            organization_id=access.organization_id,
            workspace_id=access.workspace_id,
            source_id=command.source_id,
            source_version=command.source_version,
            title=command.title,
            source_type=command.source_type,
            data_classification=command.data_classification,
            storage_uri=command.storage_uri,
            content_hash=digest,
            content=command.content,
            document_id=command.document_id,
            status=SourceStatus.RECEIVED,
            created_by=access.actor_id,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            classification = self._classifications.get(source_key)
            if classification is not None and classification != command.data_classification:
                raise SourceRegistryError("source classification is immutable")
            existing = self._versions.get(version_key)
            if existing is not None:
                if existing.content_hash == digest:
                    return existing.model_copy(deep=True)
                raise SourceRegistryError("immutable source version already exists")
            self._classifications[source_key] = command.data_classification
            self._versions[version_key] = record
        await self._record(access, command.source_id, "source.version.registered", "RECEIVED")
        return record.model_copy(deep=True)

    async def verify(
        self,
        source_id: str,
        source_version: str,
        *,
        access: KnowledgeAccessContext,
    ) -> SourceVersionRecord:
        self._require_scope(access, "scope.sources.verify")
        key = (access.tenant_id, access.workspace_id, source_id, source_version)
        async with self._lock:
            current = self._versions.get(key)
            if current is None or current.organization_id != access.organization_id:
                raise SourceRegistryError("source version was not found in workspace")
            if current.status is SourceStatus.RETIRED:
                raise SourceRegistryError("retired source cannot be verified")
            updated = current.model_copy(
                update={
                    "status": SourceStatus.VERIFIED,
                    "verified_by": access.actor_id,
                    "verified_at": datetime.now(UTC),
                }
            )
            self._versions[key] = updated
        await self._record(access, source_id, "source.version.verified", "VERIFIED")
        return updated.model_copy(deep=True)

    async def search_context(
        self,
        query: str,
        *,
        access: KnowledgeAccessContext,
        limit: int = 12,
        max_characters: int = 12_000,
    ) -> dict[str, Any]:
        self._require_scope(access, "scope.sources.read")
        if not query.strip():
            raise SourceRegistryError("source query must not be blank")
        if not 1 <= limit <= 50:
            raise SourceRegistryError("source result limit must be between 1 and 50")
        if not 1 <= max_characters <= 60_000:
            raise SourceRegistryError("source character budget must be between 1 and 60000")
        terms = set(re.findall(r"[a-z0-9_]+", query.casefold()))
        candidates: list[tuple[int, datetime, SourceVersionRecord, tuple[Any, ...]]] = []
        for record in self._versions.values():
            if (
                record.tenant_id != access.tenant_id
                or record.organization_id != access.organization_id
                or record.workspace_id != access.workspace_id
                or record.status is not SourceStatus.VERIFIED
                or self._CLASSIFICATION_RANK[record.data_classification]
                > self._CLASSIFICATION_RANK[access.data_classification]
            ):
                continue
            for chunk in _chunk_content(
                record.source_id,
                record.source_version,
                record.content,
            ):
                score = sum(term in chunk[3].casefold() for term in terms)
                if score:
                    candidates.append((score, record.created_at, record, chunk))
        candidates.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[3][0]))

        items: list[dict[str, Any]] = []
        evidence_refs: list[dict[str, Any]] = []
        remaining = max_characters
        for _score, _created, source, chunk in candidates:
            if len(items) >= limit or remaining <= 0:
                break
            chunk_index, citation_key, anchor, content = chunk
            excerpt = content[:remaining].rstrip()
            if not excerpt:
                break
            evidence_id = _canonical_id(
                "evidence",
                f"{citation_key}:{hashlib.sha256(excerpt.encode('utf-8')).hexdigest()}",
            )
            evidence = self._evidence.register(
                {
                    "tenant_id": access.tenant_id,
                    "organization_id": access.organization_id,
                    "workspace_id": access.workspace_id,
                    "evidence_id": evidence_id,
                    "source_id": source.source_id,
                    "uri": source.storage_uri,
                    "captured_at": source.created_at.isoformat().replace("+00:00", "Z"),
                    "content_hash": source.content_hash,
                    "source_version": source.source_version,
                    "anchor": anchor,
                    "excerpt": excerpt,
                    "data_classification": source.data_classification,
                    "validation_status": "VALID",
                    "metadata": {
                        "citation_key": citation_key,
                        "chunk_index": chunk_index,
                    },
                }
            )
            evidence_refs.append(evidence)
            items.append(
                {
                    "key": citation_key,
                    "value": excerpt,
                    "source_id": source.source_id,
                    "evidence_id": evidence_id,
                    "source_version": source.source_version,
                    "content_hash": source.content_hash,
                    "anchor": anchor,
                    "data_classification": source.data_classification,
                }
            )
            remaining -= len(excerpt)
        now = datetime.now(UTC)
        payload = {
            "context_id": _canonical_id(
                "context",
                f"{access.correlation_id}:{query}:{len(items)}",
            ),
            "tenant_id": access.tenant_id,
            "organization_id": access.organization_id,
            "workspace_id": access.workspace_id,
            "actor_id": access.actor_id,
            "correlation_id": access.correlation_id,
            "scope_refs": sorted(access.scope_refs),
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "items": items,
            "evidence_refs": evidence_refs,
        }
        validated = self._contracts.validate(CONTEXT_BUNDLE_SCHEMA, payload)
        await self._record(
            access,
            str(validated["context_id"]),
            "source.context.retrieved",
            "SUCCESS",
        )
        return validated

    def list_versions(self, *, access: KnowledgeAccessContext) -> tuple[SourceVersionRecord, ...]:
        self._require_scope(access, "scope.sources.read")
        return tuple(
            record.model_copy(deep=True)
            for record in self._versions.values()
            if record.tenant_id == access.tenant_id
            and record.organization_id == access.organization_id
            and record.workspace_id == access.workspace_id
        )

    @staticmethod
    def _require_scope(access: KnowledgeAccessContext, scope: str) -> None:
        if scope not in access.scope_refs:
            raise SourceRegistryError(f"required scope is missing: {scope}")

    async def _record(
        self,
        access: KnowledgeAccessContext,
        entity_id: str,
        event_type: str,
        outcome: str,
    ) -> None:
        await self._audit.append(
            AuditEvent(
                event_type=event_type,
                entity_type="source",
                entity_id=entity_id,
                tenant_id=access.tenant_id,
                organization_id=access.organization_id,
                workspace_id=access.workspace_id,
                actor_id=access.actor_id,
                correlation_id=access.correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                reason="Governed source authority operation",
            )
        )


def _chunk_content(
    source_id: str,
    source_version: str,
    content: str,
    *,
    max_characters: int = 1_500,
) -> list[tuple[int, str, str, str]]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        lines = [content.strip()]
    chunks: list[tuple[int, str, str, str]] = []
    current: list[tuple[int, str]] = []
    current_size = 0
    for line_number, line in enumerate(lines, 1):
        parts = [
            line[index : index + max_characters]
            for index in range(0, len(line), max_characters)
        ]
        for part in parts:
            next_size = current_size + len(part) + (1 if current else 0)
            if current and (len(current) >= 8 or next_size > max_characters):
                chunks.append(_make_chunk(source_id, source_version, len(chunks), current))
                current, current_size = [], 0
            current.append((line_number, part))
            current_size += len(part) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append(_make_chunk(source_id, source_version, len(chunks), current))

    occurrences: dict[str, int] = {}
    unique: list[tuple[int, str, str, str]] = []
    for index, citation, anchor, text in chunks:
        occurrences[citation] = occurrences.get(citation, 0) + 1
        occurrence = occurrences[citation]
        unique_citation = citation if occurrence == 1 else f"{citation}-P{occurrence}"
        unique.append((index, unique_citation, anchor, text))
    return unique


def _make_chunk(
    source_id: str,
    source_version: str,
    index: int,
    lines: list[tuple[int, str]],
) -> tuple[int, str, str, str]:
    start, end = lines[0][0], lines[-1][0]
    citation = f"{source_id}@{source_version}#L{start}-L{end}"
    return index, citation, f"lines {start}-{end}", "\n".join(item[1] for item in lines)


def _canonical_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"
