"""Owner leads request Director direction through the configured company route."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import insert, select, update

from alos.domains.record_repository import conflict
from alos.identity import Principal
from alos.processes.authority import revalidate
from alos.processes.subjects import owner_domain, snapshot
from alos.security.errors import PlatformError

if TYPE_CHECKING:
    from alos.processes.service import ProcessService


async def request_direction(
    service: "ProcessService", principal: Principal, process_id: str, reason: str
) -> dict[str, Any]:
    async with service.repository.factory() as session, session.begin():
        await revalidate(service.repository, session, principal)
        record = await service.load(session, principal, process_id)
        if (
            record["workspace_id"] != principal.workspace_id
            or "DIVISION_LEAD" not in principal.roles
            or f"{owner_domain(record['business_type'])}.write" not in principal.permissions
        ):
            raise PlatformError(
                "PROCESS_DIRECTION_DENIED", "Kepala divisi pemilik diperlukan.", status_code=403
            )
        policy = await service.policy(session, principal, record["business_type"])
        _, digest = await snapshot(
            service.repository,
            session,
            principal,
            record["business_type"],
            record["subject_id"],
            writing=True,
        )
        table = await service.table(session, "es")
        record = dict(
            (
                await session.execute(
                    select(table).where(table.c.process_id == process_id).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if record["status"] not in {"READY", "IN_PROGRESS", "COMPLETED"}:
            raise conflict("Pengajuan perlu diperbaiki sebelum meminta arahan.")
        if record["subject_snapshot"] != digest or record["policy_version"] != policy["version"]:
            raise conflict("Catatan atau aturan berubah; pemeriksaan harus diulang.")
        steps = await service.steps(session, process_id)
        if record.get("direction_reason") or any(
            item["kind"] == "DECISION" and item["role"] == "EXECUTIVE" for item in steps
        ):
            raise conflict("Pengajuan sudah memerlukan keputusan Direktur.")
        target = policy["routes"].get("executive")
        if not target:
            raise conflict("Divisi Direktur belum ditentukan dalam aturan perusahaan.")
        workspaces = await service.repository.table(session, "core", "workspaces")
        if not await session.scalar(
            select(workspaces.c.workspace_id)
            .where(
                *service.company(workspaces, principal),
                workspaces.c.workspace_id == target,
                workspaces.c.active.is_(True),
            )
            .with_for_update(read=True)
        ):
            raise conflict("Divisi Direktur tidak aktif.")
        now = datetime.now(UTC)
        ready = all(item["status"] == "COMPLETED" for item in steps)
        hours = policy["rules"].get("due_hours", {}).get("EXECUTIVE_DIRECTION")
        steps_table = await service.table(session, "_steps")
        await session.execute(
            insert(steps_table).values(
                step_id=uuid4().hex,
                process_id=process_id,
                revision=record["revision"],
                position=max(item["position"] for item in steps) + 1,
                code="EXECUTIVE_DIRECTION",
                kind="DECISION",
                workspace_id=target,
                role="EXECUTIVE",
                permission="approval.approve",
                independent=True,
                instruction="Berikan arahan atas pengajuan kepala divisi",
                reason=reason,
                status="READY" if ready else "PENDING",
                due_at=now + timedelta(hours=hours) if ready and hours else None,
            )
        )
        changes = {
            "direction_reason": reason,
            "status": "READY" if ready else record["status"],
            "updated_at": now,
        }
        await session.execute(
            update(table).where(table.c.process_id == process_id).values(**changes)
        )
        record.update(changes)
        await service.history(session, record, principal, "DIRECTION_REQUESTED", reason, [])
        return await service.project(session, record, principal)
