"""Upload, extraction and source lineage reuse Backend document and job authority."""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select

from alos.audit import AuditEvent
from alos.context.policy import maximum_classification
from alos.documents.extraction import extract, validate_filename
from alos.documents.object_store import DocumentObjectStore
from alos.domains.record_repository import RecordRepository, conflict
from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.jobs.models import EnqueueJob, Job, JobType
from alos.jobs.sql_repository import SqlJobRepository
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import revalidate
from alos.security.errors import PlatformError

RANK = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}


class DocumentIngestionService:
    def __init__(
        self,
        records: RecordRepository,
        work: SharedWorkService,
        objects: DocumentObjectStore,
        limit: int,
    ) -> None:
        self.records, self.work, self.objects, self.limit = records, work, objects, limit
        self.jobs = SqlJobRepository(records.factory)

    @staticmethod
    def permission(principal: Principal, *, write: bool = False) -> None:
        allowed = {"document.version", "work.write"} if write else {"document.read", "work.read"}
        if not principal.active or not principal.permissions.intersection(allowed):
            raise PlatformError(
                "DOCUMENT_ACCESS_DENIED", "Kewenangan dokumen diperlukan.", status_code=403
            )

    def classification(self, principal: Principal, document: dict[str, Any]) -> None:
        if RANK[document["data_classification"]] > RANK[maximum_classification(principal)]:
            raise PlatformError(
                "DOCUMENT_ACCESS_DENIED", "Dokumen tidak dapat diakses.", status_code=404
            )

    async def upload(
        self,
        principal: Principal,
        document_id: str,
        filename: str,
        content_type: str,
        version: str,
        data: bytes,
    ) -> dict[str, Any]:
        self.permission(principal, write=True)
        try:
            kind = validate_filename(filename, content_type)
        except ValueError as exc:
            raise PlatformError(
                "DOCUMENT_TYPE_INVALID", "Nama atau jenis berkas tidak didukung.", status_code=422
            ) from exc
        if not 0 < len(data) <= self.limit:
            raise PlatformError(
                "DOCUMENT_SIZE_INVALID", "Ukuran berkas tidak didukung.", status_code=413
            )
        digest = hashlib.sha256(data).hexdigest()
        async with self.records.factory() as session, session.begin():
            await revalidate(self.records, session, principal)
            document = await self.work._document_row(session, principal, document_id, lock=True)
            self.classification(principal, document)
            if document["status"] != "DRAFT":
                raise conflict("Unggahan versi hanya tersedia untuk dokumen konsep.")
            uploads = await self.records.table(session, "core", "document_uploads")
            existing = (
                (
                    await session.execute(
                        select(uploads).where(
                            *self.records.scope(uploads, principal),
                            uploads.c.document_id == document_id,
                            uploads.c.version == version,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["content_hash"] != digest or existing["source_type"] != kind:
                    raise conflict("Versi dokumen telah memiliki berkas yang berbeda.")
                return await self.status(principal, existing["upload_id"])
            versions = await self.records.table(session, "core", "document_versions")
            if await session.scalar(
                select(versions.c.record_id).where(
                    *self.records.scope(versions, principal),
                    versions.c.document_id == document_id,
                    versions.c.version == version,
                )
            ):
                raise conflict("Versi dokumen sudah ada dan tidak dapat diganti.")
            boundary = (principal.tenant_id, principal.organization_id, principal.workspace_id)
            await asyncio.to_thread(self.objects.put, boundary, data)
            upload_id, source_id = uuid4().hex, uuid4().hex
            now = datetime.now(UTC)
            job = await self.jobs.enqueue_in_session(
                session,
                EnqueueJob(
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    job_type=JobType.DOCUMENT_EXTRACTION,
                    payload={"upload_id": upload_id, "source_id": source_id},
                    correlation_id=current_correlation_id(),
                    idempotency_key="document-upload:" + upload_id,
                    owner_actor_id=principal.actor_id,
                ),
            )
            values = {
                "upload_id": upload_id,
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "document_id": document_id,
                "version": version,
                "filename": filename,
                "source_type": kind,
                "content_hash": digest,
                "size_bytes": len(data),
                "job_id": job.job_id,
                "created_by": principal.actor_id,
                "created_at": now,
            }
            await session.execute(insert(uploads).values(**values))
            await self.audit(session, principal, upload_id, job.correlation_id, "uploaded")
            return self.project(values, job)

    @staticmethod
    def project(upload: dict[str, Any], job: Job) -> dict[str, Any]:
        return {
            "upload_id": upload["upload_id"],
            "document_id": upload["document_id"],
            "version": upload["version"],
            "filename": upload["filename"],
            "content_hash": "sha256:" + upload["content_hash"],
            "size_bytes": upload["size_bytes"],
            "status": job.status.value,
            "attempts": job.attempts,
            "safe_error_code": job.safe_error_code,
            "source_id": (job.result or {}).get("source_id"),
        }

    async def status(self, principal: Principal, upload_id: str) -> dict[str, Any]:
        self.permission(principal)
        async with self.records.factory() as session:
            uploads = await self.records.table(session, "core", "document_uploads")
            row = (
                (
                    await session.execute(
                        select(uploads).where(
                            *self.records.scope(uploads, principal),
                            uploads.c.upload_id == upload_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise PlatformError(
                    "DOCUMENT_UPLOAD_NOT_FOUND", "Unggahan tidak tersedia.", status_code=404
                )
            doc = await self.work._document_row(session, principal, row["document_id"])
            self.classification(principal, doc)
            return self.project(dict(row), await self.jobs.get(row["job_id"]))

    async def extract_job(self, job: Job) -> dict[str, Any]:
        async with self.records.factory() as session:
            uploads = await self.records.table(session, "core", "document_uploads")
            row = (
                (
                    await session.execute(
                        select(uploads).where(
                            uploads.c.upload_id == job.payload["upload_id"],
                            uploads.c.tenant_id == job.tenant_id,
                            uploads.c.organization_id == job.organization_id,
                            uploads.c.workspace_id == job.workspace_id,
                            uploads.c.job_id == job.job_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            upload = dict(row)
        data = await asyncio.to_thread(
            self.objects.read,
            (job.tenant_id, job.organization_id, job.workspace_id),
            upload["content_hash"],
            self.limit,
        )
        text = await asyncio.to_thread(extract, data, upload["source_type"])
        async with self.records.factory() as session, session.begin():
            # Use current memberships, rather than the uploader's old authority snapshot.
            await self.jobs._running(session, job.job_id, job.locked_by, job.attempts)
            members = await self.records.table(session, "core", "workspace_memberships")
            member = (
                (
                    await session.execute(
                        select(members).where(
                            members.c.actor_id == job.owner_actor_id,
                            members.c.workspace_id == job.workspace_id,
                            members.c.tenant_id == job.tenant_id,
                            members.c.organization_id == job.organization_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            principal = Principal(
                str(job.owner_actor_id),
                job.tenant_id,
                job.organization_id,
                job.workspace_id,
                permissions=frozenset(member["permission_refs"]),
                roles=frozenset(member["roles"]),
                scopes=frozenset(member["scope_refs"]),
            )
            await revalidate(self.records, session, principal)
            self.permission(principal, write=True)
            doc = await self.work._document_row(
                session, principal, upload["document_id"], lock=True
            )
            self.classification(principal, doc)
            versions = await self.records.table(session, "core", "document_versions")
            existing = await session.scalar(
                select(versions.c.source_id).where(
                    *self.records.scope(versions, principal),
                    versions.c.document_id == upload["document_id"],
                    versions.c.version == upload["version"],
                )
            )
            source_id = str(job.payload["source_id"])
            if existing:
                if existing != source_id:
                    raise conflict("Versi dokumen memiliki sumber berbeda.")
                return {"source_id": source_id, "version": upload["version"]}
            if doc["status"] != "DRAFT":
                raise conflict(
                    "Dokumen telah masuk pemeriksaan; versi unggahan tidak dapat ditambahkan."
                )
            sources = await self.records.table(session, "core", "sources")
            source_versions = await self.records.table(session, "core", "source_versions")
            scope = {
                "tenant_id": job.tenant_id,
                "organization_id": job.organization_id,
                "workspace_id": job.workspace_id,
            }
            now = datetime.now(UTC)
            await session.execute(
                insert(sources).values(
                    **scope,
                    source_id=source_id,
                    title=doc["title"],
                    source_type=upload["source_type"],
                    data_classification=doc["data_classification"],
                    document_id=doc["document_id"],
                    created_by=principal.actor_id,
                    created_at=now,
                )
            )
            await session.execute(
                insert(source_versions).values(
                    **scope,
                    source_id=source_id,
                    source_version="1",
                    storage_uri=f"alos://document-objects/{upload['upload_id']}",
                    content_hash="sha256:" + upload["content_hash"],
                    extracted_content=text,
                    status="VERIFIED",
                    verified_by="system.document-extraction",
                    verified_at=now,
                    created_by=principal.actor_id,
                    created_at=now,
                )
            )
            await self.work.create_document_version_in_session(
                session,
                principal,
                doc["document_id"],
                {
                    "version": upload["version"],
                    "source_id": source_id,
                    "source_version": "1",
                },
            )
            await self.audit(
                session, principal, upload["upload_id"], job.correlation_id, "extracted"
            )
            return {"source_id": source_id, "version": upload["version"]}

    async def get_document_content(self, principal: Principal, document_id: str) -> dict[str, Any]:
        self.permission(principal)
        async with self.records.factory() as session, session.begin():
            await revalidate(self.records, session, principal)
            doc = await self.work._document_row(session, principal, document_id)
            self.classification(principal, doc)
            if doc["status"] != "APPROVED":
                raise conflict("Dokumen perlu disetujui sebelum digunakan sebagai sumber bisnis.")
            versions = await self.records.table(session, "core", "document_versions")
            sources = await self.records.table(session, "core", "source_versions")
            row = (
                (
                    await session.execute(
                        select(versions.c.version, sources)
                        .join(
                            sources,
                            (sources.c.source_id == versions.c.source_id)
                            & (sources.c.source_version == versions.c.source_version),
                        )
                        .where(
                            *self.records.scope(versions, principal),
                            *self.records.scope(sources, principal),
                            versions.c.document_id == document_id,
                            sources.c.status == "VERIFIED",
                            sources.c.extracted_content.is_not(None),
                        )
                        .order_by(versions.c.created_at.desc(), versions.c.version.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise conflict("Isi dokumen belum tersedia dari pekerjaan ekstraksi.")
            content = str(row["extracted_content"])
            return {
                "document_id": document_id,
                "title": doc["title"],
                "version": row["version"],
                "source_id": row["source_id"],
                "source_version": row["source_version"],
                "content_hash": row["content_hash"],
                "content": content[:12_000],
                "truncated": len(content) > 12_000,
                "data_classification": doc["data_classification"],
                "instruction_authority": False,
            }

    async def audit(
        self, session: Any, principal: Principal, upload_id: str, correlation_id: str, action: str
    ) -> None:
        await self.records.audit.append_in_session(
            session,
            AuditEvent(
                event_type="document." + action,
                entity_type="document_upload",
                entity_id=upload_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
                outcome="SUCCEEDED",
                occurred_at=datetime.now(UTC),
                reason="Berkas dan asal versi dokumen tercatat",
                metadata={"roles": sorted(principal.roles)},
            ),
        )
