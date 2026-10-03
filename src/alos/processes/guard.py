"""Existing canonical decisions and commands consume completed process prerequisites."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, conflict
from alos.identity import Principal
from alos.processes.execution import verify_execution
from alos.processes.subjects import snapshot


async def require_reviews(
    repository: RecordRepository,
    session: AsyncSession,
    principal: Principal,
    business_type: str,
    subject_id: str,
    *,
    required: bool = False,
) -> None:
    policies = await repository.table(session, "core", "business_process_policies")
    policy = (
        (
            await session.execute(
                select(policies)
                .where(
                    *repository.scope(policies, principal),
                    policies.c.business_type == business_type,
                    policies.c.active.is_(True),
                )
                .with_for_update(read=True)
            )
        )
        .mappings()
        .first()
    )
    # Existing independent approvals remain compatible before company rules are configured.
    if policy is None:
        if required:
            raise conflict("Aturan pemeriksaan kebutuhan divisi harus dikonfigurasi.")
        return
    _, digest = await snapshot(repository, session, principal, business_type, subject_id)
    processes = await repository.table(session, "core", "business_processes")
    process = (
        (
            await session.execute(
                select(processes)
                .where(
                    *repository.scope(processes, principal),
                    processes.c.business_type == business_type,
                    processes.c.subject_id == subject_id,
                )
                .with_for_update(read=True)
            )
        )
        .mappings()
        .first()
    )
    if process is None or process["status"] != "COMPLETED":
        raise conflict("Pemeriksaan alur proses belum selesai.")
    if process["policy_version"] != policy["version"] or process["subject_snapshot"] != digest:
        raise conflict("Pemeriksaan harus diulang karena catatan atau aturan berubah.")
    # Access and assets can change after a human confirmation. Final owner commands
    # read the actual state again within the same transaction.
    checks = {
        "ONBOARDING": ("IT_ACCESS", "GA_READINESS"),
        "OFFBOARDING": ("ASSET_RETURN", "IT_REVOCATION"),
    }
    for code in checks.get(business_type, ()):
        await verify_execution(repository, session, dict(process), {"code": code}, dict(policy))
