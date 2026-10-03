"""Scoped analytics built from canonical business records and existing projections."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from typing import cast as type_cast

from sqlalchemy import DateTime, and_, case, cast, func, select, text
from sqlalchemy.sql import ColumnElement

from alos.domains.record_repository import RecordRepository, authorize
from alos.domains.sales.records import PIPELINE_EDGES
from alos.identity import Principal

Granularity = Literal["DAY", "MONTH", "QUARTER", "YEAR"]


class AnalyticsPeriodError(ValueError):
    """The requested date range is outside analytics support."""


STATUS_LABELS = {
    "ACTIVE": "Aktif",
    "APPROVED": "Disetujui",
    "APPLIED": "Melamar",
    "ASSIGNED": "Ditugaskan",
    "BLOCKED": "Terhambat",
    "CONFIRMED": "Dikonfirmasi",
    "CANCELLED": "Dibatalkan",
    "CLOSED": "Ditutup",
    "COMPLETED": "Selesai",
    "DRAFT": "Draft",
    "DEPLOYED": "Diterapkan",
    "EXPIRED": "Berakhir",
    "FAILED": "Gagal",
    "FOLLOW_UP": "Perlu Tindak Lanjut",
    "HIGH": "Tinggi",
    "IN_PROGRESS": "Sedang Berjalan",
    "IN_REVIEW": "Sedang Diperiksa",
    "INTERVIEW": "Wawancara",
    "INVESTIGATING": "Sedang Diselidiki",
    "LOW": "Rendah",
    "MEDIUM": "Sedang",
    "NEW": "Baru",
    "NOT_STARTED": "Belum Dimulai",
    "OPEN": "Terbuka",
    "OFFERED": "Penawaran",
    "ON_HOLD": "Ditunda",
    "OVERDUE": "Terlambat",
    "PLANNED": "Direncanakan",
    "QUALIFIED": "Terkualifikasi",
    "READY": "Siap",
    "REJECTED": "Tidak Dilanjutkan",
    "REMEDIATING": "Sedang Ditangani",
    "RESOLVED": "Diselesaikan",
    "ROLLED_BACK": "Dikembalikan",
    "SCREENING": "Penyaringan",
    "TERMINATED": "Diakhiri",
    "WITHDRAWN": "Mengundurkan Diri",
    "CRITICAL": "Kritis",
    "VERIFIED": "Terverifikasi",
}

MAX_RANGE_DAYS = {"DAY": 366, "MONTH": 3660, "QUARTER": 10980, "YEAR": 36500}


def _period_start(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError("Database returned an unsupported analytics bucket.")


def _amount(value: Any) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Analytics amounts must be finite decimals.")
    return format(number, "f")


def _category_label(value: Any) -> str:
    raw = str(value)
    if raw in STATUS_LABELS:
        return STATUS_LABELS[raw]
    return "Status belum dikenali" if raw.isupper() and "_" in raw else raw


def _strategy_value(observation: Any, unit: str) -> str | int | float | None:
    if not isinstance(observation, dict) or observation.get("value") is None:
        return None
    try:
        number = Decimal(str(observation["value"]))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if unit == "AMOUNT":
        return _amount(number)
    return int(number) if unit == "COUNT" else float(number)


def _date_bucket(column: ColumnElement[Any], granularity: Granularity) -> ColumnElement[Any]:
    if isinstance(column.type, DateTime) and column.type.timezone:
        return func.date_trunc(granularity.lower(), column, "UTC")
    timestamp = column if isinstance(column.type, DateTime) else cast(column, DateTime())
    return func.date_trunc(granularity.lower(), timestamp)


def _date_bounds(column: ColumnElement[Any], date_from: date, date_to: date) -> tuple[Any, Any]:
    next_day = date_to + timedelta(days=1)
    if isinstance(column.type, DateTime):
        tz = UTC if column.type.timezone else None
        return (
            datetime.combine(date_from, time.min, tzinfo=tz),
            datetime.combine(next_day, time.min, tzinfo=tz),
        )
    return date_from, next_day


def _period(date_from: date, date_to: date, granularity: Granularity) -> dict[str, Any]:
    if date_from > date_to:
        raise AnalyticsPeriodError("Analytics start date must not follow its end date.")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS[granularity]:
        raise AnalyticsPeriodError("Requested analytics period exceeds its supported range.")
    return {"from": date_from.isoformat(), "to": date_to.isoformat(), "granularity": granularity}


def _series(
    code: str,
    label: str,
    unit: str,
    source: str,
    points: list[dict[str, Any]],
    *,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "unit": unit,
        "available": available,
        "source": source,
        "points": points if available else [],
    }


def _breakdown(
    code: str,
    label: str,
    unit: str,
    source: str,
    items: list[dict[str, Any]],
    *,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "unit": unit,
        "available": available,
        "source": source,
        "items": items if available else [],
    }


def _comparison(
    code: str,
    label: str,
    unit: str,
    source: str,
    items: list[dict[str, Any]],
    *,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "unit": unit,
        "available": available,
        "source": source,
        "items": items if available else [],
    }


async def _count_breakdown(
    repository: RecordRepository,
    session: Any,
    principal: Principal,
    domain: str,
    table_name: str,
    column_name: str,
    *,
    code: str,
    label: str,
    source: str,
    filters: tuple[Any, ...] = (),
) -> dict[str, Any]:
    table = await repository.table(session, domain, table_name)
    dimension = table.c[column_name]
    rows = (
        await session.execute(
            select(dimension, func.count())
            .where(*repository.scope(table, principal), *filters)
            .group_by(dimension)
            .order_by(dimension)
        )
    ).all()
    return _breakdown(
        code,
        label,
        "COUNT",
        source,
        [
            {
                "code": str(value),
                "label": _category_label(value),
                "value": int(count),
            }
            for value, count in rows
            if value is not None
        ],
    )


async def _sales_funnel_breakdown(
    repository: RecordRepository,
    session: Any,
    principal: Principal,
) -> dict[str, Any]:
    opportunities = await repository.table(session, "sales", "opportunities")
    stage_rows = (
        await session.execute(
            select(opportunities.c.stage, func.count())
            .where(*repository.scope(opportunities, principal), opportunities.c.status == "OPEN")
            .group_by(opportunities.c.stage)
        )
    ).all()
    counts = {str(stage): int(count) for stage, count in stage_rows if stage is not None}

    closing_records = await repository.table(session, "sales", "closings")
    closing_count = int(
        (
            await session.execute(
                select(func.count()).where(
                    *repository.scope(closing_records, principal),
                    closing_records.c.status == "COMPLETED",
                )
            )
        ).scalar_one()
    )

    stages = tuple(PIPELINE_EDGES) + tuple(
        stage for stage in PIPELINE_EDGES.values() if stage not in PIPELINE_EDGES
    )
    stage_labels = {
        "Lead": "Lead",
        "Qualified": "Terkualifikasi",
        "Survey": "Survei",
        "Booking": "Booking",
    }
    items = [
        {
            "code": stage,
            "label": stage_labels.get(stage, "Tahap lain"),
            "value": counts.pop(stage, 0),
        }
        for stage in stages
    ]
    unknown_count = sum(counts.values())
    if unknown_count:
        items.append({"code": "other_stage", "label": "Tahap lain", "value": unknown_count})
    items.append({"code": "closing", "label": "Closing", "value": closing_count})
    return _breakdown(
        "sales_funnel",
        "Sales Funnel",
        "COUNT",
        "Tahap peluang terbuka dan Closing yang telah selesai",
        items,
    )


async def _trend(
    repository: RecordRepository,
    session: Any,
    principal: Principal,
    domain: str,
    table_name: str,
    date_column: str,
    date_from: date,
    date_to: date,
    granularity: Granularity,
    *,
    count_code: str,
    count_label: str,
    source: str,
    filters: tuple[Any, ...] = (),
    company_scope: bool = False,
    amount_column: str | None = None,
    amount_code: str | None = None,
    amount_label: str | None = None,
) -> list[dict[str, Any]]:
    table = await repository.table(session, domain, table_name)
    bucket = _date_bucket(table.c[date_column], granularity)
    lower, upper = _date_bounds(table.c[date_column], date_from, date_to)
    expressions: list[Any] = [
        bucket.label("period"),
        func.count().label("record_count"),
    ]
    if amount_column:
        expressions.append(func.sum(table.c[amount_column]).label("amount_total"))
    rows = (
        (
            await session.execute(
                select(*expressions)
                .where(
                    *repository.scope(table, principal, company=company_scope),
                    table.c[date_column] >= lower,
                    table.c[date_column] < upper,
                    *filters,
                )
                .group_by(bucket)
                .order_by(bucket)
            )
        )
        .mappings()
        .all()
    )
    count_points = [
        {"period": _period_start(row["period"]), "value": int(row["record_count"])} for row in rows
    ]
    result = [_series(count_code, count_label, "COUNT", source, count_points)]
    if amount_column and amount_code and amount_label:
        amount_points = [
            {"period": _period_start(row["period"]), "value": _amount(row["amount_total"])}
            for row in rows
            if row["amount_total"] is not None
        ]
        result.append(_series(amount_code, amount_label, "AMOUNT", source, amount_points))
    return result


async def _finance_due_breakdown(
    repository: RecordRepository,
    session: Any,
    principal: Principal,
    today: date,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for table_name, prefix, label, late_operator in (
        ("receivables", "receivable", "Piutang", "<"),
        ("payables", "payable", "Utang", "<="),
    ):
        table = await repository.table(session, "finance", table_name)
        overdue = table.c.due_date < today if late_operator == "<" else table.c.due_date <= today
        not_due = table.c.due_date >= today if late_operator == "<" else table.c.due_date > today
        values = (
            (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        and_(table.c.status == "OPEN", not_due),
                                        table.c.outstanding_amount,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ).label("not_due"),
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        and_(table.c.status == "OPEN", overdue),
                                        table.c.outstanding_amount,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ).label("due"),
                    ).where(*repository.scope(table, principal))
                )
            )
            .mappings()
            .one()
        )
        items.extend(
            (
                {
                    "code": f"{prefix}_not_due",
                    "label": f"{label} belum jatuh tempo",
                    "value": _amount(values["not_due"]),
                },
                {
                    "code": f"{prefix}_due",
                    "label": f"{label} {'terlambat' if prefix == 'receivable' else 'jatuh tempo'}",
                    "value": _amount(values["due"]),
                },
            )
        )
    return _breakdown(
        "payment_exposure",
        "Piutang dan utang menurut jatuh tempo",
        "AMOUNT",
        "Saldo tagihan terbuka dan tanggal jatuh tempo",
        items,
    )


async def business_analytics(
    repository: RecordRepository,
    principal: Principal,
    domain: str,
    date_from: date,
    date_to: date,
    granularity: Granularity,
    *,
    executive: bool = False,
    shared_work: Any | None = None,
    strategy_service: Any | None = None,
) -> dict[str, Any]:
    """Return bounded SQL aggregates and already-authoritative linked projections."""
    auth_domain = "sales" if domain == "executive" else domain
    authorize(principal, auth_domain, "read", executive=executive)
    period = _period(date_from, date_to, granularity)
    now = datetime.now(UTC)
    series: list[dict[str, Any]] = []
    breakdowns: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    async with repository.factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await repository.workspace(session, principal)

        if domain in {"sales", "executive"}:
            closings = await repository.table(session, "sales", "closings")
            series.extend(
                await _trend(
                    repository,
                    session,
                    principal,
                    "sales",
                    "closings",
                    "closing_date",
                    date_from,
                    date_to,
                    granularity,
                    count_code="closing_count",
                    count_label="Jumlah Closing selesai",
                    amount_code="closing_value",
                    amount_label="Nilai Closing",
                    source="Closing yang telah selesai",
                    filters=(closings.c.status == "COMPLETED",),
                    company_scope=executive,
                    amount_column="amount",
                )
            )
            if domain == "sales":
                breakdowns.append(await _sales_funnel_breakdown(repository, session, principal))

        if domain == "property":
            for table_name, column_name, code, label, source in (
                (
                    "project_milestones",
                    "status",
                    "milestones_by_status",
                    "Milestone menurut status",
                    "Milestone proyek",
                ),
                (
                    "quality_ncrs",
                    "status",
                    "ncr_by_status",
                    "NCR menurut status",
                    "Catatan NCR mutu",
                ),
            ):
                breakdowns.append(
                    await _count_breakdown(
                        repository,
                        session,
                        principal,
                        "property",
                        table_name,
                        column_name,
                        code=code,
                        label=label,
                        source=source,
                    )
                )

        if domain == "finance":
            breakdowns.append(
                await _finance_due_breakdown(repository, session, principal, now.date())
            )
            for table_name, code, label, source in (
                (
                    "receivable_payments",
                    "receipts",
                    "Pembayaran piutang",
                    "Pembayaran piutang yang tercatat",
                ),
                (
                    "payable_payments",
                    "payments",
                    "Pembayaran utang",
                    "Pembayaran utang yang tercatat",
                ),
            ):
                table = await repository.table(session, "finance", table_name)
                series.extend(
                    await _trend(
                        repository,
                        session,
                        principal,
                        "finance",
                        table_name,
                        "payment_date",
                        date_from,
                        date_to,
                        granularity,
                        count_code=f"{code}_count",
                        count_label=f"Jumlah {label.lower()}",
                        amount_code=f"{code}_amount",
                        amount_label=f"Nilai {label.lower()}",
                        source=source,
                        filters=(table.c.status == "POSTED",),
                        amount_column="amount",
                    )
                )

        if domain == "legal":
            breakdowns.append(
                await _count_breakdown(
                    repository,
                    session,
                    principal,
                    "legal",
                    "contracts",
                    "status",
                    code="contracts_by_status",
                    label="Kontrak menurut status",
                    source="Status kontrak tercatat",
                )
            )
            contracts = await repository.table(session, "legal", "contracts")
            days_to_expiry = contracts.c.end_date - now.date()
            expiry_bucket = case(
                (days_to_expiry < 0, "expired"),
                (days_to_expiry <= 30, "within_30_days"),
                (days_to_expiry <= 60, "within_31_60_days"),
                (days_to_expiry <= 90, "within_61_90_days"),
                else_="over_90_days",
            )
            expiry_rows = (
                await session.execute(
                    select(expiry_bucket, func.count())
                    .where(
                        *repository.scope(contracts, principal),
                        contracts.c.status == "ACTIVE",
                        contracts.c.end_date.is_not(None),
                    )
                    .group_by(expiry_bucket)
                )
            ).all()
            expiry_labels = {
                "expired": "Sudah berakhir",
                "within_30_days": "Dalam 30 hari",
                "within_31_60_days": "31-60 hari",
                "within_61_90_days": "61-90 hari",
                "over_90_days": "Lebih dari 90 hari",
            }
            breakdowns.append(
                _breakdown(
                    "contract_expiry",
                    "Kontrak aktif menurut tanggal akhir",
                    "COUNT",
                    "Tanggal akhir kontrak aktif",
                    [
                        {
                            "code": str(code),
                            "label": expiry_labels[str(code)],
                            "value": type_cast(int, count),
                        }
                        for code, count in expiry_rows
                    ],
                )
            )

        if domain == "hr":
            breakdowns.append(
                await _count_breakdown(
                    repository,
                    session,
                    principal,
                    "hr",
                    "candidates",
                    "status",
                    code="candidate_funnel",
                    label="Kandidat menurut tahap rekrutmen",
                    source="Status kandidat terkini",
                )
            )
            series.extend(
                await _trend(
                    repository,
                    session,
                    principal,
                    "hr",
                    "candidates",
                    "created_at",
                    date_from,
                    date_to,
                    granularity,
                    count_code="candidate_count",
                    count_label="Kandidat tercatat",
                    source="Kandidat yang didaftarkan",
                )
            )

        if domain == "it":
            breakdowns.extend(
                (
                    await _count_breakdown(
                        repository,
                        session,
                        principal,
                        "it",
                        "incidents",
                        "status",
                        code="incidents_by_status",
                        label="Insiden menurut status",
                        source="Status insiden tercatat",
                    ),
                    await _count_breakdown(
                        repository,
                        session,
                        principal,
                        "it",
                        "security_findings",
                        "severity",
                        code="security_by_severity",
                        label="Temuan keamanan menurut tingkat keparahan",
                        source="Tingkat keparahan temuan keamanan",
                    ),
                    await _count_breakdown(
                        repository,
                        session,
                        principal,
                        "it",
                        "releases",
                        "status",
                        code="releases_by_status",
                        label="Rilis menurut status",
                        source="Status rilis tercatat",
                    ),
                )
            )
            series.extend(
                await _trend(
                    repository,
                    session,
                    principal,
                    "it",
                    "incidents",
                    "created_at",
                    date_from,
                    date_to,
                    granularity,
                    count_code="incident_count",
                    count_label="Insiden dicatat",
                    source="Waktu pencatatan insiden",
                )
            )

        if domain in {"property", "executive"}:
            can_read_projects = bool({"project.read", "work.read"} & principal.permissions)
            if shared_work is not None and can_read_projects:
                rows = await shared_work.list_projects(principal, status=None, search=None)
                presented = await shared_work.present(principal, "PROJECT", rows)
                progress_items = [
                    {
                        "code": str(row.get("project_id")),
                        "label": str(row.get("name") or "Proyek tanpa nama"),
                        "value": float(row["progress_percentage"]),
                        "target_value": None,
                        "actual_value": None,
                        "forecast_value": None,
                    }
                    for row in presented[:20]
                    if row.get("progress_percentage") is not None
                ]
                comparisons.append(
                    _comparison(
                        "project_progress",
                        "Progres proyek",
                        "PERCENT",
                        "Progres tugas pada maksimal 20 proyek terbaru yang dapat diakses",
                        progress_items,
                    )
                )
            else:
                comparisons.append(
                    _comparison(
                        "project_progress",
                        "Progres proyek",
                        "PERCENT",
                        "Proyek yang dapat diakses pada ruang kerja aktif",
                        [],
                        available=False,
                    )
                )

        if domain == "executive" and strategy_service is not None:
            from alos.domains.strategy.models import LifecycleState
            from alos.domains.strategy.read_model import target_detail

            targets = await strategy_service.list_targets(principal)
            target_items_by_unit: dict[str, list[dict[str, Any]]] = {}
            for target in targets:
                if target.lifecycle_state is not LifecycleState.ACTIVE:
                    continue
                detail = await target_detail(strategy_service, principal, target)
                selected = detail.get("selected_observations") or {}
                observations = {
                    kind: selected.get(kind) for kind in ("target", "actual", "forecast")
                }
                units = {
                    str(item.get("unit"))
                    for item in observations.values()
                    if isinstance(item, dict) and item.get("value") is not None
                }
                if len(units) != 1:
                    continue
                source_unit = next(iter(units))
                target_unit = "AMOUNT" if source_unit == "IDR" else source_unit
                if target_unit not in {"COUNT", "AMOUNT", "PERCENT"}:
                    continue

                target_value = _strategy_value(observations["target"], target_unit)
                actual_value = _strategy_value(observations["actual"], target_unit)
                forecast_value = _strategy_value(observations["forecast"], target_unit)
                if target_value is None and actual_value is None and forecast_value is None:
                    continue
                target_items_by_unit.setdefault(target_unit, []).append(
                    {
                        "code": str(detail["target"].get("target_id")),
                        "label": str(detail["target"].get("name") or "Target strategi"),
                        "value": actual_value,
                        "target_value": target_value,
                        "actual_value": actual_value,
                        "forecast_value": forecast_value,
                    }
                )
            for target_unit, target_items in sorted(target_items_by_unit.items()):
                comparisons.append(
                    _comparison(
                        f"strategy_target_actual_{target_unit.lower()}",
                        "Target, aktual, dan perkiraan perusahaan",
                        target_unit,
                        "Observasi target strategi perusahaan yang dipilih",
                        target_items,
                    )
                )

    return {
        "domain": domain,
        "generated_at": now.isoformat(),
        "period": period,
        "series": series,
        "breakdowns": breakdowns,
        "comparisons": comparisons,
    }
