"""Document metadata authority; document reasoning remains in GENESIS."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from alos.audit import AuditEvent, AuditSink
from alos.documents.models import DataClassification, DocumentMetadata, DocumentVersion
from alos.sources import KnowledgeAccessContext


class DocumentRegistryError(ValueError):
    pass


class DocumentRegistry:
    def __init__(self, audit: AuditSink) -> None:
        self._audit = audit
        self._documents: dict[tuple[str, str, str], DocumentMetadata] = {}
        self._versions: dict[tuple[str, str, str, str], DocumentVersion] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        document_id: str,
        title: str,
        category: str,
        data_classification: DataClassification,
        access: KnowledgeAccessContext,
    ) -> DocumentMetadata:
        self._require_scope(access, "scope.documents.write")
        document = DocumentMetadata(
            document_id=document_id,
            tenant_id=access.tenant_id,
            organization_id=access.organization_id,
            workspace_id=access.workspace_id,
            title=title,
            category=category,
            data_classification=data_classification,
            owner_actor_id=access.actor_id,
            created_at=datetime.now(UTC),
        )
        key = (access.tenant_id, access.workspace_id, document_id)
        async with self._lock:
            existing = self._documents.get(key)
            if existing is not None:
                if (
                    existing.organization_id == document.organization_id
                    and existing.title == document.title
                    and existing.category == document.category
                    and existing.data_classification == document.data_classification
                    and existing.owner_actor_id == document.owner_actor_id
                ):
                    return existing.model_copy(deep=True)
                raise DocumentRegistryError("document_id already exists")
            self._documents[key] = document
        await self._record(access, document_id, "document.registered", "DRAFT")
        return document.model_copy(deep=True)

    async def add_version(
        self,
        version: DocumentVersion,
        *,
        access: KnowledgeAccessContext,
    ) -> DocumentVersion:
        self._require_scope(access, "scope.documents.write")
        document_key = (access.tenant_id, access.workspace_id, version.document_id)
        version_key = (*document_key, version.version)
        async with self._lock:
            if document_key not in self._documents:
                raise DocumentRegistryError("document was not found in workspace")
            existing = self._versions.get(version_key)
            if existing is not None:
                if existing == version:
                    return existing.model_copy(deep=True)
                raise DocumentRegistryError("immutable document version already exists")
            self._versions[version_key] = version
        await self._record(
            access,
            version.document_id,
            "document.version.registered",
            version.version,
        )
        return version.model_copy(deep=True)

    def get(
        self,
        document_id: str,
        *,
        access: KnowledgeAccessContext,
    ) -> tuple[DocumentMetadata, tuple[DocumentVersion, ...]]:
        self._require_scope(access, "scope.documents.read")
        key = (access.tenant_id, access.workspace_id, document_id)
        document = self._documents.get(key)
        if document is None or document.organization_id != access.organization_id:
            raise DocumentRegistryError("document was not found in workspace")
        versions = tuple(
            value.model_copy(deep=True)
            for version_key, value in self._versions.items()
            if version_key[:3] == key
        )
        return document.model_copy(deep=True), versions

    @staticmethod
    def _require_scope(access: KnowledgeAccessContext, scope: str) -> None:
        if scope not in access.scope_refs:
            raise DocumentRegistryError(f"required scope is missing: {scope}")

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
                entity_type="document",
                entity_id=entity_id,
                tenant_id=access.tenant_id,
                organization_id=access.organization_id,
                workspace_id=access.workspace_id,
                actor_id=access.actor_id,
                correlation_id=access.correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                reason="Authoritative document metadata operation",
            )
        )
