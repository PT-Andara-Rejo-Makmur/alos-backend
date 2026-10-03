"""Business needs use the existing Factory and remain subject to release governance."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alos.audit import AuditEvent
from alos.domains.record_repository import RecordRepository, conflict
from alos.factory.service import FactoryOrchestrator
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.persistence.models import RegistryDefinitionRecord, ReleaseRecord
from alos.processes.authority import revalidate
from alos.processes.guard import require_reviews
from alos.processes.subjects import SUBJECTS
from alos.security.errors import PlatformError


class CapabilityBusinessRequests:
    def __init__(self, repository: RecordRepository) -> None:
        self.repository = repository

    @staticmethod
    def authorize(principal: Principal, *, resolve: bool = False) -> None:
        permission = "work.write" if resolve else "work.read"
        if permission not in principal.permissions or (
            resolve and "DIVISION_LEAD" not in principal.roles
        ):
            raise PlatformError(
                "CAPABILITY_REQUEST_DENIED",
                "Kewenangan aktif pada ruang kerja diperlukan.",
                status_code=403,
            )

    async def event(
        self,
        session: AsyncSession,
        principal: Principal,
        identity: str,
        event: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.repository.audit.append_in_session(
            session,
            AuditEvent(
                event_type="capability.business_request." + event,
                entity_type="capability_business_request",
                entity_id=identity,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=current_correlation_id(),
                outcome="SUCCEEDED",
                occurred_at=datetime.now(UTC),
                reason=reason,
                metadata={
                    **metadata,
                    "roles": sorted(principal.roles),
                    "permissions": sorted(principal.permissions),
                },
            ),
        )

    async def create(self, principal: Principal, values: dict[str, Any]) -> dict[str, Any]:
        if "work.write" not in principal.permissions or not principal.roles & {
            "DIVISION_MEMBER",
            "DIVISION_LEAD",
        }:
            raise PlatformError(
                "CAPABILITY_REQUEST_DENIED",
                "Kewenangan mengajukan kebutuhan diperlukan.",
                status_code=403,
            )
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            table = await self.repository.table(session, "core", "capability_business_requests")
            now = datetime.now(UTC)
            row = {
                "request_id": uuid4().hex,
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "requested_by": principal.actor_id,
                "status": "SUBMITTED",
                "created_at": now,
                "updated_at": now,
                "business_context": None,
                **values,
            }
            await session.execute(insert(table).values(**row))
            await self.event(session, principal, row["request_id"], "created", values["goal"], {})
            return await self.project(session, row)

    async def project(self, session: AsyncSession, row: dict[str, Any]) -> dict[str, Any]:
        resolutions = await self.repository.table(session, "core", "capability_request_resolutions")
        resolution = (
            (
                await session.execute(
                    select(resolutions).where(resolutions.c.request_id == row["request_id"])
                )
            )
            .mappings()
            .first()
        )
        result = resolution["result"] if resolution else None
        governance = []
        for ref in (
            (result or {}).get("registry_result", {}).get("registered_refs", [])
            if (result or {}).get("registry_result")
            else []
        ):
            entry = await session.scalar(
                select(RegistryDefinitionRecord).where(
                    RegistryDefinitionRecord.tenant_id == row["tenant_id"],
                    RegistryDefinitionRecord.organization_id == row["organization_id"],
                    RegistryDefinitionRecord.workspace_id == row["workspace_id"],
                    RegistryDefinitionRecord.subject_type == ref["subject_type"].lower(),
                    RegistryDefinitionRecord.subject_id == ref["identifier"],
                    RegistryDefinitionRecord.version == ref["version"],
                )
            )
            release = (
                await session.scalar(
                    select(ReleaseRecord).where(
                        ReleaseRecord.release_id == entry.release_id,
                        ReleaseRecord.tenant_id == row["tenant_id"],
                        ReleaseRecord.organization_id == row["organization_id"],
                        ReleaseRecord.workspace_id == row["workspace_id"],
                        ReleaseRecord.subject_id == ref["identifier"],
                        ReleaseRecord.subject_version == ref["version"],
                    )
                )
                if entry and entry.release_id
                else None
            )
            governance.append(
                {
                    "subject_id": ref["identifier"],
                    "version": ref["version"],
                    "registry_state": entry.lifecycle_state if entry else None,
                    "release_state": release.state if release else None,
                    "release_id": release.release_id if release else None,
                }
            )
        return dict(
            jsonable_encoder(
                {
                    **row,
                    "resolution_state": resolution["state"] if resolution else "UNRESOLVED",
                    "factory_result": result,
                    "review_id": resolution["review_id"] if resolution else None,
                    "governance": governance,
                }
            )
        )

    async def get(self, principal: Principal, identity: str) -> dict[str, Any]:
        self.authorize(principal)
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            row = await self.repository.row(
                session, "core", SUBJECTS["CAPABILITY_REQUEST"][1], principal, identity
            )
            return await self.project(session, row)

    async def list(self, principal: Principal) -> dict[str, Any]:
        self.authorize(principal)
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            table = await self.repository.table(session, "core", "capability_business_requests")
            rows = (
                (
                    await session.execute(
                        select(table)
                        .where(*self.repository.scope(table, principal))
                        .order_by(table.c.created_at.desc())
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "items": [await self.project(session, dict(row)) for row in rows],
                "can_create": "work.write" in principal.permissions
                and bool(principal.roles & {"DIVISION_MEMBER", "DIVISION_LEAD"}),
                "can_resolve": "work.write" in principal.permissions
                and "DIVISION_LEAD" in principal.roles,
            }

    async def resolve(
        self, principal: Principal, identity: str, reason: str, factory: FactoryOrchestrator
    ) -> dict[str, Any]:
        self.authorize(principal, resolve=True)

        async def current_authority() -> None:
            async with self.repository.factory() as session, session.begin():
                await revalidate(self.repository, session, principal)
                await require_reviews(
                    self.repository, session, principal, "CAPABILITY_REQUEST", identity
                )

        await current_authority()
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            row = await self.repository.row(
                session, "core", SUBJECTS["CAPABILITY_REQUEST"][1], principal, identity, lock=True
            )
            resolutions = await self.repository.table(
                session, "core", "capability_request_resolutions"
            )
            old = (
                (
                    await session.execute(
                        select(resolutions)
                        .where(resolutions.c.request_id == identity)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if old and old["state"] == "RESOLVED":
                return await self.project(session, row)
            now = datetime.now(UTC)
            if (
                old
                and old["state"] == "RESOLVING"
                and old["started_at"] > now - timedelta(minutes=5)
            ):
                raise conflict("Kebutuhan sedang dianalisis. Muat kembali untuk melihat hasilnya.")
            correlation = old["correlation_id"] if old else current_correlation_id()
            claim = {
                "state": "RESOLVING",
                "correlation_id": correlation,
                "resolved_by": principal.actor_id,
                "review_reason": reason,
                "started_at": now,
                "completed_at": None,
                "result": None,
            }
            if old:
                await session.execute(
                    update(resolutions).where(resolutions.c.request_id == identity).values(**claim)
                )
            else:
                await session.execute(insert(resolutions).values(request_id=identity, **claim))
            await self.event(session, principal, identity, "resolution_requested", reason, {})
        try:
            # Reuse Factory's scope checks, contracts and draft registration. Humans
            # supply no provider settings or permission grants.
            result = await factory.analyze(
                {
                    "requirement": "\n".join(
                        [
                            "Kebutuhan: " + row["need"],
                            "Tujuan: " + row["goal"],
                            "Konteks: " + (row["business_context"] or "Tidak ditambahkan"),
                        ]
                    )
                },
                principal=principal,
                correlation_id=correlation,
                before_register=current_authority,
            )
            await current_authority()
            async with self.repository.factory() as session, session.begin():
                await revalidate(self.repository, session, principal)
                await require_reviews(
                    self.repository, session, principal, "CAPABILITY_REQUEST", identity
                )
                await session.execute(
                    update(resolutions)
                    .where(resolutions.c.request_id == identity)
                    .values(state="RESOLVED", result=result, completed_at=datetime.now(UTC))
                )
                await self.event(
                    session,
                    principal,
                    identity,
                    "resolved",
                    reason,
                    {"decision": result["decision"], "factory_correlation_id": correlation},
                )
        except Exception:
            async with self.repository.factory() as session, session.begin():
                await session.execute(
                    update(resolutions)
                    .where(resolutions.c.request_id == identity)
                    .values(state="FAILED", completed_at=datetime.now(UTC))
                )
            raise
        return await self.get(principal, identity)
