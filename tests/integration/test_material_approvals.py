"""Action-scoped independent human decisions and atomic execution on real PostgreSQL."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from test_business_domains import Context
from test_business_domains import context as migrated_context

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def booking(ctx: Context) -> dict[str, Any]:
    customer = await ctx.create(
        "sales", "customers", {"customer_code": uuid4().hex, "name": "Buyer"}
    )
    unit = await ctx.create("property", "property-units", {"unit_code": uuid4().hex})
    return await ctx.create(
        "sales",
        "bookings",
        {
            "customer_id": customer["customer_id"],
            "property_unit_id": unit["property_unit_id"],
            "booking_date": "2026-01-01",
            "amount": "10.01",
        },
    )


async def request(
    ctx: Context,
    subject: str,
    identity: str,
    action: str,
    user: str = "member",
    expected: int = 201,
) -> dict[str, Any]:
    response = await ctx.client.post(
        "/api/v1/approvals",
        headers=ctx.headers[user],
        json={
            "subject_type": subject,
            "subject_id": identity,
            "requested_action": action,
            "reason": "Explicit human request",
        },
    )
    assert response.status_code == expected, response.text
    return response.json()


async def decide(
    ctx: Context,
    approval: dict[str, Any],
    action: str = "approve",
    user: str = "lead",
    expected: int = 200,
) -> None:
    response = await ctx.client.post(
        f"/api/v1/approvals/{approval['approval_id']}/{action}",
        headers=ctx.headers[user],
        json={"decision_reason": "Independent review"},
    )
    assert response.status_code == expected, response.text


async def execute(
    ctx: Context,
    domain: str,
    resource: str,
    identity: str,
    status: str,
    approval: dict[str, Any],
    expected: int = 200,
    user: str = "member",
) -> Any:
    response = await ctx.client.post(
        f"/api/v1/{domain}/{resource}/{identity}/transition",
        headers=ctx.headers[user],
        json={"status": status, "approval_id": approval["approval_id"]},
    )
    assert response.status_code == expected, response.text
    return response.json()


async def persisted(ctx: Context, approval: dict[str, Any]) -> dict[str, Any]:
    work = ctx.app.state.shared_work_service
    async with work._session_factory() as session:
        table = await work._table(session, "work_approvals")
        return dict(
            (
                await session.execute(
                    select(table).where(table.c.approval_id == approval["approval_id"])
                )
            )
            .mappings()
            .one()
        )


@pytest.mark.parametrize("state", ["pending", "return", "reject", "hold"])
async def test_nonapproved_never_executes(context: Context, state: str) -> None:
    row = await booking(context)
    approval = await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING")
    if state != "pending":
        await decide(context, approval, state)
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 409)
    assert (await persisted(context, approval))["consumed_at"] is None


async def test_independent_authority_scope_and_subject(context: Context) -> None:
    row, other = await booking(context), await booking(context)
    approval = await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING")
    await decide(context, approval, user="member", expected=403)
    lead_request = await request(
        context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING", "lead"
    )
    await decide(context, lead_request, expected=403)
    for foreign in ("workspace", "org", "tenant"):
        await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING", foreign, 404)
        await decide(context, approval, user=foreign, expected=404)
        await execute(
            context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 404, foreign
        )
        assert (
            await context.client.get(
                f"/api/v1/approvals/{approval['approval_id']}", headers=context.headers[foreign]
            )
        ).status_code == 404
    await request(context, "SALES_BOOKING", row["booking_id"], "WIN_OPPORTUNITY", expected=409)
    await decide(context, approval)
    await execute(context, "sales", "bookings", other["booking_id"], "CONFIRMED", approval, 409)
    await execute(context, "sales", "bookings", row["booking_id"], "CANCELLED", approval, 409)
    # Approval has no implicit business side effect.
    current = await context.client.get(
        f"/api/v1/sales/bookings/{row['booking_id']}", headers=context.headers["member"]
    )
    assert current.json()["status"] == "PENDING"
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval)
    consumed = await persisted(context, approval)
    assert consumed["consumed_at"] and consumed["consumed_by"] and consumed["transition_ref"]
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 409)
    await context.seed_legacy_status(
        "sales", "bookings", "booking_id", row["booking_id"], "PENDING"
    )
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 409)


async def test_stale_content_and_lifecycle(context: Context) -> None:
    row = await booking(context)
    approval = await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING")
    await decide(context, approval)
    response = await context.client.patch(
        f"/api/v1/sales/bookings/{row['booking_id']}",
        headers=context.headers["member"],
        json={"booking_date": "2026-01-02"},
    )
    assert response.status_code == 200
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 409)
    fresh = await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING")
    await decide(context, fresh)
    await context.transition("sales", "bookings", row["booking_id"], "CANCELLED")
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", fresh, 409)
    assert (await persisted(context, fresh))["consumed_at"] is None


async def test_atomic_audit_rollback_and_concurrent_consumption(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = await booking(context)
    approval = await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING")
    await decide(context, approval)
    repository = context.app.state.sales_service.repository

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("audit unavailable", {}, RuntimeError("isolated test"))

    original = repository.audit.append_in_session
    before = await context.audit_count("sales")
    monkeypatch.setattr(repository.audit, "append_in_session", fail)
    await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 503)
    assert (await persisted(context, approval))["consumed_at"] is None
    assert await context.audit_count("sales") == before
    current = await context.client.get(
        f"/api/v1/sales/bookings/{row['booking_id']}", headers=context.headers["member"]
    )
    assert current.json()["status"] == "PENDING"
    monkeypatch.setattr(repository.audit, "append_in_session", original)
    responses = await asyncio.gather(
        *[
            context.client.post(
                f"/api/v1/sales/bookings/{row['booking_id']}/transition",
                headers=context.headers["member"],
                json={"status": "CONFIRMED", "approval_id": approval["approval_id"]},
            )
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert await context.audit_count("sales") > before


@pytest.mark.parametrize(
    "resource,subject,initial,action,target,values",
    [
        (
            "property-units",
            "PROPERTY_UNIT",
            None,
            "RESERVE_UNIT",
            "RESERVED",
            {"unit_code": "unused"},
        ),
        (
            "change-orders",
            "PROPERTY_CHANGE_ORDER",
            "SUBMITTED",
            "APPROVE_CHANGE_ORDER",
            "APPROVED",
            {"change_number": "internal", "description": "change", "amount_delta": "12.34"},
        ),
        (
            "payment-certificates",
            "PROPERTY_PAYMENT_CERTIFICATE",
            "SUBMITTED",
            "APPROVE_PAYMENT_CERTIFICATE",
            "APPROVED",
            {"certificate_number": "internal", "period": "2026-01", "amount": "12.34"},
        ),
    ],
)
async def test_property_material_actions(
    context: Context,
    resource: str,
    subject: str,
    initial: str | None,
    action: str,
    target: str,
    values: dict[str, Any],
) -> None:
    if "unit_code" in values:
        values = {"unit_code": uuid4().hex}
    else:
        response = await context.client.post(
            "/api/v1/projects",
            headers=context.headers["lead"],
            json={"code": uuid4().hex, "name": "Project"},
        )
        assert response.status_code == 201, response.text
        values = {**values, "project_id": response.json()["project_id"]}
    row = await context.create("property", resource, values)
    identifier = {
        "property-units": "property_unit_id",
        "change-orders": "change_order_id",
        "payment-certificates": "payment_certificate_id",
    }[resource]
    if initial:
        await context.transition("property", resource, row[identifier], initial)
    approval = await request(context, subject, row[identifier], action)
    await context.transition("property", resource, row[identifier], target, 409)
    await decide(context, approval)
    await execute(context, "property", resource, row[identifier], target, approval)
    if resource == "property-units":
        sell = await request(context, subject, row[identifier], "SELL_UNIT")
        await decide(context, sell)
        await execute(context, "property", resource, row[identifier], "SOLD", sell)


async def test_budget_each_action_requires_new_approval(context: Context) -> None:
    row = await context.create(
        "finance", "budgets", {"name": "Governed budget", "fiscal_year": 2026}
    )
    await context.transition("finance", "budgets", row["budget_id"], "UNDER_REVIEW")
    for action, status in [
        ("APPROVE_BUDGET", "APPROVED"),
        ("ACTIVATE_BUDGET", "ACTIVE"),
        ("CLOSE_BUDGET", "CLOSED"),
    ]:
        await context.transition("finance", "budgets", row["budget_id"], status, 409)
        approval = await request(context, "FINANCE_BUDGET", row["budget_id"], action)
        await decide(context, approval)
        await execute(
            context, "finance", "budgets", row["budget_id"], status, approval, user="lead"
        )


async def test_sales_win_closing_and_pricing_content_snapshot(context: Context) -> None:
    booked = await booking(context)
    confirm = await request(context, "SALES_BOOKING", booked["booking_id"], "CONFIRM_BOOKING")
    await decide(context, confirm)
    await execute(context, "sales", "bookings", booked["booking_id"], "CONFIRMED", confirm)
    closing = await context.create(
        "sales",
        "closings",
        {
            "booking_id": booked["booking_id"],
            "customer_id": booked["customer_id"],
            "property_unit_id": booked["property_unit_id"],
            "amount": "10.01",
            "closing_date": "2026-01-02",
        },
    )
    complete = await request(context, "SALES_CLOSING", closing["closing_id"], "COMPLETE_CLOSING")
    await decide(context, complete)
    await execute(context, "sales", "closings", closing["closing_id"], "COMPLETED", complete)
    opportunity = await context.create(
        "sales", "opportunities", {"customer_id": booked["customer_id"], "name": "Governed win"}
    )
    for stage in ["Qualified", "Survey", "Booking"]:
        response = await context.client.post(
            f"/api/v1/sales/opportunities/{opportunity['opportunity_id']}/pipeline",
            headers=context.headers["member"],
            json={"stage": stage},
        )
        assert response.status_code == 200
    win = await request(
        context, "SALES_OPPORTUNITY", opportunity["opportunity_id"], "WIN_OPPORTUNITY"
    )
    await decide(context, win)
    await execute(context, "sales", "opportunities", opportunity["opportunity_id"], "WON", win)
    pricing = await context.create("sales", "pricings", {"name": "Recorded pricing"})
    pricing_unit = await context.create("property", "property-units", {"unit_code": uuid4().hex})
    item = await context.create(
        "sales",
        "pricing-items",
        {
            "pricing_id": pricing["pricing_id"],
            "property_unit_id": pricing_unit["property_unit_id"],
            "price": "10.01",
        },
    )
    approval = await request(context, "SALES_PRICING", pricing["pricing_id"], "ACTIVATE_PRICING")
    await decide(context, approval)
    changed = await context.client.patch(
        f"/api/v1/sales/pricing-items/{item['pricing_item_id']}",
        headers=context.headers["member"],
        json={"price": "10.02"},
    )
    assert changed.status_code == 200
    await execute(context, "sales", "pricings", pricing["pricing_id"], "ACTIVE", approval, 409)
    fresh = await request(context, "SALES_PRICING", pricing["pricing_id"], "ACTIVATE_PRICING")
    await decide(context, fresh)
    await execute(context, "sales", "pricings", pricing["pricing_id"], "ACTIVE", fresh)
    changed = await context.client.patch(
        f"/api/v1/sales/pricing-items/{item['pricing_item_id']}",
        headers=context.headers["member"],
        json={"price": "10.03"},
    )
    assert changed.status_code == 409


@pytest.mark.parametrize("phase", ["request", "decision", "consumption"])
async def test_material_audit_failure_at_each_boundary(
    context: Context, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    row = await booking(context)
    work = context.app.state.shared_work_service

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("audit unavailable", {}, RuntimeError("isolated atomicity test"))

    before = await context.audit_count("sales")
    if phase == "request":
        monkeypatch.setattr(work._audit, "append_in_session", fail)
        await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING", expected=503)
        async with work._session_factory() as session:
            table = await work._table(session, "work_approvals")
            assert (
                await session.execute(select(table).where(table.c.subject_id == row["booking_id"]))
            ).first() is None
        assert await context.audit_count("sales") == before
        return
    approval = await request(context, "SALES_BOOKING", row["booking_id"], "CONFIRM_BOOKING")
    if phase == "decision":
        before = await context.audit_count("sales")
        monkeypatch.setattr(work._audit, "append_in_session", fail)
        await decide(context, approval, expected=503)
        persisted_approval = await persisted(context, approval)
        assert (
            persisted_approval["status"] == "PENDING"
            and persisted_approval["approver_actor_id"] is None
        )
    else:
        await decide(context, approval)
        before = await context.audit_count("sales")
        monkeypatch.setattr(work._audit, "append_in_session", fail)
        await execute(context, "sales", "bookings", row["booking_id"], "CONFIRMED", approval, 503)
        assert (await persisted(context, approval))["consumed_at"] is None
    assert await context.audit_count("sales") == before
    current = await context.client.get(
        f"/api/v1/sales/bookings/{row['booking_id']}", headers=context.headers["member"]
    )
    assert current.json()["status"] == "PENDING"
