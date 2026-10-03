"""Legal receives assigned business packets and records supporting contract lineage."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import insert, select, update

from alos.audit import AuditEvent
from alos.domains.legal.records import SPECS
from alos.domains.record_repository import authorize, conflict
from alos.identity import Principal
from alos.notifications.business import enqueue_notice
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import assigned, revalidate
from alos.processes.service import ProcessService
from alos.security.errors import PlatformError


async def link_contract(
    processes: ProcessService, principal: Principal, contract_id: str, process_id: str, reason: str
) -> dict[str, Any]:
    authorize(principal, "legal", "write")
    if "DIVISION_LEAD" not in principal.roles:
        raise PlatformError(
            "CONTRACT_ORIGIN_DENIED", "Kepala divisi Legal diperlukan.", status_code=403
        )
    repository = processes.repository
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        record = await processes.load(session, principal, process_id)
        if record["business_type"] not in {"CHANGE_ORDER", "EMPLOYMENT_CONTRACT"}:
            raise conflict("Pengajuan kontrak tidak tersedia untuk jenis proses ini.")
        policy = await processes.policy(
            session,
            replace(principal, workspace_id=record["workspace_id"]),
            record["business_type"],
        )
        steps = await processes.steps(session, process_id)
        if not any(step["code"] == "LEGAL_REVIEW" and assigned(step, principal) for step in steps):
            raise PlatformError(
                "CONTRACT_ORIGIN_DENIED",
                "Pengajuan belum ditugaskan kepada Legal aktif.",
                status_code=403,
            )
        if record["policy_version"] != policy["version"] or record["status"] not in {
            "READY",
            "IN_PROGRESS",
            "RETURNED",
        }:
            raise conflict("Aturan atau pengajuan berubah; hubungan kontrak belum dapat dibuat.")
        # Match the policy -> owner record -> process -> supporting record lock order.
        from alos.processes.subjects import snapshot

        await snapshot(
            repository,
            session,
            replace(principal, workspace_id=record["workspace_id"]),
            record["business_type"],
            record["subject_id"],
        )
        table = await processes.table(session, "es")
        current = (
            (
                await session.execute(
                    select(table).where(table.c.process_id == process_id).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if current["revision"] != record["revision"] or current["status"] not in {
            "READY",
            "IN_PROGRESS",
            "RETURNED",
        }:
            raise conflict("Pengajuan telah berubah.")
        await repository.row(
            session, "legal", SPECS["contracts"], principal, contract_id, lock=True
        )
        links = await repository.table(session, "core", "business_record_links")
        where = (
            links.c.tenant_id == principal.tenant_id,
            links.c.organization_id == principal.organization_id,
            links.c.source_type
            == (
                "PROPERTY_CHANGE_ORDER"
                if record["business_type"] == "CHANGE_ORDER"
                else "HR_EMPLOYMENT_CONTRACT"
            ),
            links.c.source_id == record["subject_id"],
            links.c.target_type == "LEGAL_CONTRACT",
            links.c.relation == "GOVERNED_BY",
        )
        existing = (await session.execute(select(links).where(*where))).mappings().first()
        if existing:
            if existing["target_id"] != contract_id:
                raise conflict("Pengajuan telah dihubungkan dengan kontrak berbeda.")
            return {"items": jsonable_encoder([dict(existing)])}
        link = {
            "link_id": uuid4().hex,
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "source_workspace_id": record["workspace_id"],
            "source_type": "PROPERTY_CHANGE_ORDER"
            if record["business_type"] == "CHANGE_ORDER"
            else "HR_EMPLOYMENT_CONTRACT",
            "source_id": record["subject_id"],
            "target_workspace_id": principal.workspace_id,
            "target_type": "LEGAL_CONTRACT",
            "target_id": contract_id,
            "relation": "GOVERNED_BY",
            "process_id": process_id,
            "created_by": principal.actor_id,
            "correlation_id": current_correlation_id() or uuid4().hex,
            "created_at": datetime.now(UTC),
        }
        await session.execute(insert(links).values(**link))
        await repository.audit.append_in_session(
            session,
            AuditEvent(
                entity_type="LEGAL_CONTRACT",
                entity_id=contract_id,
                occurred_at=link["created_at"],
                correlation_id=link["correlation_id"],
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                event_type="legal.contract.business_origin",
                outcome="RECORDED",
                reason=reason,
                metadata={
                    "contract_id": contract_id,
                    "process_id": process_id,
                    "origin_id": record["subject_id"],
                },
            ),
        )
        revised_record = {**record, "status": "RETURNED", "updated_at": datetime.now(UTC)}
        await session.execute(
            update(table)
            .where(table.c.process_id == process_id)
            .values(status="RETURNED", updated_at=revised_record["updated_at"])
        )
        await processes.history(session, revised_record, principal, "CONTRACT_LINKED", reason, [])
        await enqueue_notice(repository, session, revised_record, None, "RETURNED")
        return {"items": jsonable_encoder([link])}
