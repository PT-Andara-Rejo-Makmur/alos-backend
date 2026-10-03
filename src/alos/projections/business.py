"""Small SQL aggregations; unavailable sources never become fabricated business KPIs."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from alos.domains.record_repository import RecordRepository, authorize
from alos.identity import Principal

# code, label, table, status, amount column (None means COUNT).
METRICS = {
    "sales": (
        ("new_leads", "Lead baru", "leads", "NEW", None),
        ("qualified_leads", "Lead terkualifikasi", "leads", "QUALIFIED", None),
        ("pipeline_value", "Nilai peluang terbuka", "opportunities", "OPEN", "estimated_value"),
        ("bookings", "Booking dikonfirmasi", "bookings", "CONFIRMED", None),
        ("closings", "Closing selesai", "closings", "COMPLETED", None),
        ("recorded_sales_value", "Nilai penjualan tercatat", "closings", "COMPLETED", "amount"),
    ),
    "property": (
        ("open_ncr", "Ketidaksesuaian terbuka", "quality_ncrs", ("OPEN", "IN_PROGRESS"), None),
        (
            "pending_change_orders",
            "Perubahan menunggu keputusan",
            "change_orders",
            "SUBMITTED",
            None,
        ),
        (
            "pending_payment_certificates",
            "Sertifikat menunggu keputusan",
            "payment_certificates",
            "SUBMITTED",
            None,
        ),
    ),
    "finance": (
        ("receivables", "Piutang belum dibayar", "receivables", "OPEN", "outstanding_amount"),
        ("payables", "Utang belum dibayar", "payables", "OPEN", "outstanding_amount"),
        ("open_reconciliation", "Rekonsiliasi terbuka", "reconciliations", "OPEN", None),
    ),
    "legal": (
        ("contracts_needing_review", "Kontrak perlu diperiksa", "contracts", "IN_REVIEW", None),
        ("open_risks", "Risiko terbuka", "risks", ("OPEN", "IN_REVIEW"), None),
        ("open_cases", "Kasus terbuka", "cases", ("OPEN", "IN_REVIEW"), None),
    ),
    "hr": (
        ("active_headcount", "Karyawan aktif", "employees", "ACTIVE", None),
        ("open_recruitment", "Rekrutmen terbuka", "recruitments", "OPEN", None),
        ("onboarding", "Karyawan dalam orientasi", "onboardings", ("OPEN", "IN_PROGRESS"), None),
        ("leave_pending", "Cuti menunggu keputusan", "leave_requests", "PENDING", None),
        (
            "performance_review_pending",
            "Penilaian perlu diperiksa",
            "performance_reviews",
            "IN_REVIEW",
            None,
        ),
    ),
    "it": (
        ("active_systems", "Sistem aktif", "systems", "ACTIVE", None),
        ("open_incidents", "Insiden belum selesai", "incidents", ("OPEN", "INVESTIGATING"), None),
        (
            "open_security_findings",
            "Temuan keamanan belum selesai",
            "security_findings",
            ("OPEN", "IN_REVIEW", "REMEDIATING"),
            None,
        ),
        ("releases_ready", "Rilis siap dilaksanakan", "releases", "READY", None),
        ("releases_verified", "Rilis terverifikasi", "releases", "VERIFIED", None),
    ),
}
UNAVAILABLE = {
    "property": (("construction_progress", "Kemajuan konstruksi", "PERCENT"),),
    "finance": (
        ("cash_position", "Posisi kas", "AMOUNT"),
        ("budget_utilization", "Pemakaian anggaran", "PERCENT"),
    ),
    "legal": (("expiring_contracts", "Kontrak mendekati akhir", "COUNT"),),
    "it": (("backup_restore_readiness", "Kesiapan pemulihan", "PERCENT"),),
}


async def business_summary(
    repository: RecordRepository, principal: Principal, domain: str, *, executive: bool = False
) -> dict[str, Any]:
    authorize(principal, domain, "read", executive=executive)
    now = datetime.now(UTC)
    metrics: list[dict[str, Any]] = []
    async with repository.factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await repository.workspace(session, principal)
        for code, label, name, status, column in METRICS[domain]:
            table = await repository.table(session, domain, name)
            state = table.c.employment_status if name == "employees" else table.c.status
            condition = state.in_(status) if isinstance(status, tuple) else state == status
            expression = func.sum(table.c[column]) if column else func.count()
            value = await session.scalar(
                select(expression).where(
                    *repository.scope(table, principal, company=executive),
                    condition,
                )
            )
            # SUM with no source amounts stays unavailable, rather than pretending to be zero.
            metrics.append(
                {
                    "code": code,
                    "label": label,
                    "value": str(value) if isinstance(value, Decimal) else value,
                    "unit": "AMOUNT" if column else "COUNT",
                    "available": value is not None,
                    "source": name,
                }
            )
        for code, label, unit in UNAVAILABLE.get(domain, ()):
            metrics.append(
                {
                    "code": code,
                    "label": label,
                    "value": None,
                    "unit": unit,
                    "available": False,
                    "source": None,
                }
            )
        if domain == "finance":
            for name, code, label, comparator in (
                ("receivables", "overdue_receivables", "Piutang terlambat", "before"),
                ("payables", "due_payables", "Utang jatuh tempo", "through"),
            ):
                table = await repository.table(session, domain, name)
                due = (
                    table.c.due_date < now.date()
                    if comparator == "before"
                    else table.c.due_date <= now.date()
                )
                value = await session.scalar(
                    select(func.sum(table.c.outstanding_amount)).where(
                        *repository.scope(table, principal, company=executive),
                        table.c.status == "OPEN",
                        due,
                    )
                )
                metrics.append(
                    {
                        "code": code,
                        "label": label,
                        "value": str(value) if value is not None else None,
                        "unit": "AMOUNT",
                        "available": value is not None,
                        "source": name,
                    }
                )
    return {"domain": domain, "generated_at": now.isoformat(), "metrics": metrics}
