from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Column, Date, DateTime, MetaData, Table, select
from sqlalchemy.dialects import postgresql

from alos.projections.business_analytics import (
    AnalyticsPeriodError,
    _amount,
    _category_label,
    _date_bounds,
    _date_bucket,
    _period,
    _period_start,
    _strategy_value,
)


def test_analytics_period_limits_and_explicit_granularity() -> None:
    assert _period(date(2026, 1, 1), date(2026, 1, 1), "DAY") == {
        "from": "2026-01-01",
        "to": "2026-01-01",
        "granularity": "DAY",
    }
    with pytest.raises(AnalyticsPeriodError):
        _period(date(2026, 1, 2), date(2026, 1, 1), "DAY")
    with pytest.raises(AnalyticsPeriodError):
        _period(date(2024, 1, 1), date(2026, 1, 1), "DAY")


def test_amounts_remain_exact_and_non_finite_values_are_rejected() -> None:
    assert _amount(Decimal("98765432109876543210.05")) == "98765432109876543210.05"
    assert _strategy_value({"value": "25.75"}, "AMOUNT") == "25.75"
    assert _strategy_value({"value": "NaN"}, "AMOUNT") is None


def test_time_buckets_use_utc_and_date_buckets_do_not_shift_timezone() -> None:
    metadata = MetaData()
    table = Table(
        "analytics_sources",
        metadata,
        Column("created_at", DateTime(timezone=True)),
        Column("closing_date", Date),
    )
    timestamp_sql = str(
        select(_date_bucket(table.c.created_at, "MONTH")).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    date_sql = str(
        select(_date_bucket(table.c.closing_date, "MONTH")).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "date_trunc('month', analytics_sources.created_at, 'UTC')" in timestamp_sql
    assert "CAST(analytics_sources.closing_date AS TIMESTAMP WITHOUT TIME ZONE)" in date_sql
    lower, upper = _date_bounds(table.c.created_at, date(2026, 10, 3), date(2026, 10, 3))
    assert lower == datetime(2026, 10, 3, tzinfo=UTC)
    assert upper == datetime(2026, 10, 4, tzinfo=UTC)


def test_bucket_start_and_display_labels_are_human_readable() -> None:
    assert _period_start(datetime(2026, 3, 1, tzinfo=UTC)) == "2026-03-01"
    assert _category_label("IN_PROGRESS") == "Sedang Berjalan"
    assert _category_label("MITIGATING") == "Sedang Ditangani"
    assert _category_label("REVIEWED") == "Sudah Diperiksa"
    assert _category_label("UNRECOGNIZED_STATE") == "Status belum dikenali"


def test_calculate_project_progress_canonical_formula() -> None:
    from alos.domains.shared_work import calculate_project_progress

    # Empty tasks -> 0
    assert calculate_project_progress([]) == 0

    # All active completed -> 100
    assert calculate_project_progress([{"status": "COMPLETED"}]) == 100

    # 1 completed, 1 open -> 50%
    assert calculate_project_progress([{"status": "COMPLETED"}, {"status": "OPEN"}]) == 50

    # Cancelled tasks are excluded from denominator:
    # 1 completed, 1 open, 1 cancelled -> 1 / 2 = 50%
    assert (
        calculate_project_progress(
            [
                {"status": "COMPLETED"},
                {"status": "OPEN"},
                {"status": "CANCELLED"},
            ]
        )
        == 50
    )

    # Only cancelled tasks -> 0
    assert calculate_project_progress([{"status": "CANCELLED"}]) == 0

    # Rounding: 1 / 3 = 33%
    assert (
        calculate_project_progress(
            [{"status": "COMPLETED"}, {"status": "OPEN"}, {"status": "BLOCKED"}]
        )
        == 33
    )


@pytest.mark.asyncio
async def test_list_company_projects_progress_authority_guard() -> None:
    from unittest.mock import MagicMock

    from alos.domains.shared_work import SharedWorkService
    from alos.identity import DataScope, Principal
    from alos.security.errors import PlatformError

    service = SharedWorkService(session_factory=MagicMock())

    # 1. Non-executive denied
    non_exec = Principal(
        actor_id="actor_1",
        tenant_id="tenant_1",
        organization_id="org_1",
        workspace_id="ws_1",
        roles=frozenset(["DIVISION_LEAD"]),
        permissions=frozenset(["work.read", "project.read"]),
        data_scope=DataScope.COMPANY,
    )
    with pytest.raises(PlatformError) as exc:
        await service.list_company_projects_progress(non_exec)
    assert exc.value.code == "EXECUTIVE_ROLE_DENIED"

    # 2. Executive without company data scope denied
    workspace_scoped_exec = Principal(
        actor_id="actor_1",
        tenant_id="tenant_1",
        organization_id="org_1",
        workspace_id="ws_1",
        roles=frozenset(["EXECUTIVE"]),
        permissions=frozenset(["work.read", "project.read"]),
        data_scope=DataScope.WORKSPACE,
    )
    with pytest.raises(PlatformError) as exc:
        await service.list_company_projects_progress(workspace_scoped_exec)
    assert exc.value.code == "EXECUTIVE_SCOPE_DENIED"

    # 3. Executive without read permissions denied
    no_perm_exec = Principal(
        actor_id="actor_1",
        tenant_id="tenant_1",
        organization_id="org_1",
        workspace_id="ws_1",
        roles=frozenset(["EXECUTIVE"]),
        permissions=frozenset(["navigation.read"]),
        data_scope=DataScope.COMPANY,
    )
    with pytest.raises(PlatformError) as exc:
        await service.list_company_projects_progress(no_perm_exec)
    assert exc.value.code == "WORK_PERMISSION_DENIED"
