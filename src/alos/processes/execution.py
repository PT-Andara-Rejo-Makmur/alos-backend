"""Read actual owner state before accepting execution confirmations."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, conflict


async def verify_execution(
    repository: RecordRepository,
    session: AsyncSession,
    record: dict[str, Any],
    step: dict[str, Any],
    policy: dict[str, Any],
) -> None:
    employee_id = record["packet"].get("employee_id", record["subject_id"])
    employees = await repository.table(session, "hr", "employees")
    actor_id = await session.scalar(
        select(employees.c.actor_id)
        .where(
            employees.c.tenant_id == record["tenant_id"],
            employees.c.organization_id == record["organization_id"],
            employees.c.workspace_id == record["workspace_id"],
            employees.c.employee_id == employee_id,
        )
        .with_for_update(read=True)
    )
    if step["code"] == "IT_ACCESS":
        accounts = await repository.table(session, "core", "auth_accounts")
        members = await repository.table(session, "core", "workspace_memberships")
        now = datetime.now(UTC)
        account = await session.scalar(
            select(accounts.c.actor_id)
            .where(
                accounts.c.actor_id == actor_id,
                accounts.c.tenant_id == record["tenant_id"],
                accounts.c.organization_id == record["organization_id"],
                accounts.c.active.is_(True),
                accounts.c.administrative_state == "ENABLED",
                accounts.c.activation_state == "ACTIVATED",
            )
            .with_for_update(read=True)
        )
        member = await session.scalar(
            select(members.c.actor_id)
            .where(
                members.c.actor_id == actor_id,
                members.c.tenant_id == record["tenant_id"],
                members.c.organization_id == record["organization_id"],
                members.c.active.is_(True),
                members.c.workspace_id == policy["routes"]["owner"],
                members.c.revoked_at.is_(None),
                members.c.effective_at <= now,
                or_(members.c.expires_at.is_(None), members.c.expires_at > now),
            )
            .with_for_update(read=True)
        )
        if account is None or member is None:
            raise conflict("Akun dan akses divisi karyawan belum aktif melalui Identity.")
    elif step["code"] == "IT_REVOCATION":
        await verify_execution(repository, session, record, {"code": "ASSET_RETURN"}, policy)
        for name in ("auth_accounts", "workspace_memberships", "auth_sessions"):
            table = await repository.table(session, "core", name)
            active = await session.scalar(
                select(table.c.actor_id)
                .where(
                    table.c.actor_id == actor_id,
                    table.c.tenant_id == record["tenant_id"],
                    table.c.organization_id == record["organization_id"],
                    table.c.active.is_(True),
                )
                .limit(1)
                .with_for_update(read=True)
            )
            if active:
                raise conflict("Akun, membership dan sesi harus dicabut melalui Identity.")
    elif step["code"] in {"GA_READINESS", "ASSET_RETURN"}:
        handovers = await repository.table(session, "hr", "asset_handovers")
        latest = (
            (
                await session.execute(
                    select(handovers)
                    .where(
                        handovers.c.tenant_id == record["tenant_id"],
                        handovers.c.organization_id == record["organization_id"],
                        handovers.c.workspace_id == record["workspace_id"],
                        handovers.c.employee_id == employee_id,
                    )
                    .distinct(handovers.c.inventory_item_id)
                    .order_by(
                        handovers.c.inventory_item_id,
                        handovers.c.handover_on.desc(),
                        handovers.c.created_at.desc(),
                        handovers.c.asset_handover_id.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        if step["code"] == "ASSET_RETURN":
            if any(row["event"] != "RETURNED" for row in latest):
                raise conflict("Serah terima pengembalian aset karyawan belum lengkap.")
        else:
            facilities = await repository.table(session, "hr", "facility_requests")
            completed = await session.scalar(
                select(facilities.c.facility_request_id)
                .where(
                    facilities.c.tenant_id == record["tenant_id"],
                    facilities.c.organization_id == record["organization_id"],
                    facilities.c.workspace_id == record["workspace_id"],
                    facilities.c.facility_request_id == record["packet"].get("facility_request_id"),
                    facilities.c.status == "COMPLETED",
                )
                .with_for_update(read=True)
            )
            if completed is None and not any(row["event"] == "GIVEN" for row in latest):
                raise conflict("Kesiapan fasilitas atau serah terima aset belum tercatat.")
