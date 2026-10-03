"""Qualified demand, unit review, financing evidence and atomic closing on PostgreSQL."""

from uuid import uuid4

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, configure, submit
from test_material_approvals import decide, execute, request

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def test_sales_lead_to_kpr_and_closing(context: Context) -> None:
    ctx = context
    customer = await ctx.create(
        "sales", "customers", {"customer_code": uuid4().hex, "name": "Pembeli rumah"}
    )
    lead = await ctx.create(
        "sales",
        "leads",
        {"customer_id": customer["customer_id"], "source": "Kunjungan", "interest": "Rumah"},
    )
    await ctx.transition("sales", "leads", lead["lead_id"], "FOLLOW_UP")
    await ctx.transition("sales", "leads", lead["lead_id"], "QUALIFIED")
    opportunity = await ctx.create(
        "sales",
        "opportunities",
        {
            "customer_id": customer["customer_id"],
            "lead_id": lead["lead_id"],
            "name": "Pembelian rumah",
            "stage": "Lead",
            "estimated_value": "125.00",
        },
    )
    unit = await ctx.create("property", "property-units", {"unit_code": uuid4().hex})
    booking = await ctx.create(
        "sales",
        "bookings",
        {
            "customer_id": customer["customer_id"],
            "property_unit_id": unit["property_unit_id"],
            "opportunity_id": opportunity["opportunity_id"],
            "booking_date": "2026-01-01",
            "amount": "125.00",
        },
        user="member",
    )
    await configure(ctx, "BOOKING")
    process = await submit(ctx, "BOOKING", booking["booking_id"])
    approval = await request(ctx, "SALES_BOOKING", booking["booking_id"], "CONFIRM_BOOKING")
    await decide(ctx, approval, expected=409)
    await action(ctx, process, "lead", 0)
    await decide(ctx, approval)
    await execute(ctx, "sales", "bookings", booking["booking_id"], "CONFIRMED", approval)
    reserved = await ctx.client.get(
        f"/api/v1/property/property-units/{unit['property_unit_id']}", headers=ctx.headers["lead"]
    )
    assert reserved.json()["status"] == "RESERVED"
    assert reserved.json()["reservation_booking_id"] == booking["booking_id"]
    financing = await ctx.create(
        "sales",
        "financing-contexts",
        {
            "booking_id": booking["booking_id"],
            "payment_method": "KPR",
            "bank_reference": "Bank mitra / pengajuan 018",
            "required_document_notes": "Identitas dan bukti pendapatan",
            "next_action": "Lengkapi dokumen bank",
        },
    )
    for user in ("workspace", "org", "tenant"):
        denied = await ctx.client.get(
            f"/api/v1/sales/financing-contexts/{financing['financing_id']}",
            headers=ctx.headers[user],
        )
        assert denied.status_code == 404, denied.text
    closing = await ctx.create(
        "sales",
        "closings",
        {
            "booking_id": booking["booking_id"],
            "customer_id": customer["customer_id"],
            "property_unit_id": unit["property_unit_id"],
            "closing_date": "2026-02-01",
            "amount": "125.00",
        },
    )
    closing_approval = await request(
        ctx, "SALES_CLOSING", closing["closing_id"], "COMPLETE_CLOSING"
    )
    await decide(ctx, closing_approval)
    await execute(
        ctx, "sales", "closings", closing["closing_id"], "COMPLETED", closing_approval, 409
    )
    await ctx.transition("sales", "financing-contexts", financing["financing_id"], "BANK_REVIEW")
    await ctx.transition(
        "sales", "financing-contexts", financing["financing_id"], "SP3K_ISSUED", 409
    )
    updated = await ctx.client.patch(
        f"/api/v1/sales/financing-contexts/{financing['financing_id']}",
        headers=ctx.headers["lead"],
        json={
            "sp3k_reference": "SP3K-018",
            "sp3k_on": "2026-01-15",
            "akad_on": "2026-01-30",
            "next_action": "Catat Closing setelah akad",
        },
    )
    assert updated.status_code == 200, updated.text
    await ctx.transition("sales", "financing-contexts", financing["financing_id"], "SP3K_ISSUED")
    await ctx.transition("sales", "financing-contexts", financing["financing_id"], "AKAD_COMPLETED")
    await execute(ctx, "sales", "closings", closing["closing_id"], "COMPLETED", closing_approval)
    sold = await ctx.client.get(
        f"/api/v1/property/property-units/{unit['property_unit_id']}", headers=ctx.headers["lead"]
    )
    assert sold.json()["status"] == "SOLD"
    assert sold.json()["reservation_booking_id"] == booking["booking_id"]
