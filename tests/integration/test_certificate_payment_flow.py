"""Property evidence becomes one reviewed obligation, payment and bank reconciliation."""

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, configure, submit
from test_material_approvals import decide, execute, request
from test_strategy_planning_e2e import _register_and_login

pytestmark = pytest.mark.asyncio(loop_scope="module")
database_context = migrated_context


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def context(database_context: Context) -> AsyncIterator[Context]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=database_context.app), base_url="http://test"
    ) as client:
        yield Context(database_context.app, client, database_context.headers)


async def test_certificate_payable_payment_and_bidirectional_lineage(context: Context) -> None:
    ctx = context
    finance_headers, _ = await _register_and_login(
        ctx.client,
        email="finance-payment@business.test",
        tenant_id="tenant_business",
        organization_id="org_business",
        workspace_id="workspace_else",
        roles=["DIVISION_MEMBER"],
        permissions=[
            "finance.read",
            "finance.write",
            "work.read",
            "work.write",
            "approval.read",
            "approval.request",
        ],
    )
    ctx.headers["finance_member"] = finance_headers
    project = await ctx.client.post(
        "/api/v1/projects",
        headers=ctx.headers["lead"],
        json={"code": uuid4().hex, "name": "Proyek konstruksi"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    package = await ctx.create(
        "property",
        "construction-packages",
        {"project_id": project_id, "package_code": uuid4().hex, "name": "Pekerjaan konstruksi"},
    )
    await ctx.transition(
        "property", "construction-packages", package["construction_package_id"], "IN_PROGRESS"
    )
    progress = await ctx.create(
        "property",
        "construction-updates",
        {
            "construction_package_id": package["construction_package_id"],
            "update_date": "2026-10-02",
            "progress_percent": "50.00",
            "summary": "Pekerjaan dan volume telah diperiksa",
        },
    )
    certificate = await ctx.create(
        "property",
        "payment-certificates",
        {
            "project_id": project_id,
            "certificate_number": uuid4().hex,
            "period": "2026-10",
            "amount": "25.00",
            "construction_update_id": progress["construction_update_id"],
        },
    )
    await ctx.transition(
        "property", "payment-certificates", certificate["payment_certificate_id"], "SUBMITTED"
    )
    await configure(ctx, "PAYMENT_CERTIFICATE")
    process = await submit(ctx, "PAYMENT_CERTIFICATE", certificate["payment_certificate_id"])
    decision = await request(
        ctx,
        "PROPERTY_PAYMENT_CERTIFICATE",
        certificate["payment_certificate_id"],
        "APPROVE_PAYMENT_CERTIFICATE",
    )
    await decide(ctx, decision, expected=409)
    process = await action(ctx, process, "lead", 0)
    process = await action(ctx, process, "workspace", 1)
    await decide(ctx, decision)
    await execute(
        ctx,
        "property",
        "payment-certificates",
        certificate["payment_certificate_id"],
        "APPROVED",
        decision,
    )
    path = f"/api/v1/finance/payment-certificates/{process['process_id']}/payable"
    payable_result = await ctx.client.post(
        path, headers=ctx.headers["workspace"], json={"due_date": "2026-10-02"}
    )
    assert payable_result.status_code == 200, payable_result.text
    payable = payable_result.json()
    replay = await ctx.client.post(
        path, headers=ctx.headers["workspace"], json={"due_date": "2026-10-02"}
    )
    assert replay.json()["payable_id"] == payable["payable_id"]
    assert payable["amount"] == "25.00" and payable["outstanding_amount"] == "25.00"
    payment_body = {
        "payable_id": payable["payable_id"],
        "payment_date": "2026-10-02",
        "amount": "25.00",
        "reference": "BANK-PC-001",
    }
    premature = await ctx.client.post(
        "/api/v1/finance/payable-payments", headers=finance_headers, json=payment_body
    )
    assert premature.status_code == 409, premature.text
    decision = await request(
        ctx, "FINANCE_PAYABLE", payable["payable_id"], "AUTHORIZE_PAYABLE", user="finance_member"
    )
    await decide(ctx, decision, user="workspace")
    await execute(
        ctx, "finance", "payables", payable["payable_id"], "OPEN", decision, user="finance_member"
    )
    payment = await ctx.create("finance", "payable-payments", payment_body, "finance_member")
    settled = await ctx.client.get(
        f"/api/v1/finance/payables/{payable['payable_id']}", headers=finance_headers
    )
    assert settled.json()["outstanding_amount"] == "0.00" and settled.json()["status"] == "PAID"
    account = await ctx.create(
        "finance",
        "bank-accounts",
        {"account_name": "Operasional", "bank_name": "Bank"},
        "workspace",
    )
    bank_transaction = await ctx.create(
        "finance",
        "bank-transactions",
        {
            "bank_account_id": account["bank_account_id"],
            "transaction_date": "2026-10-02",
            "reference": payment_body["reference"],
            "direction": "OUT",
            "amount": "25.00",
        },
        "workspace",
    )
    linked = await ctx.client.post(
        f"/api/v1/finance/payable-payments/{payment['payment_id']}/bank-transaction",
        headers=finance_headers,
        json={
            "transaction_id": bank_transaction["transaction_id"],
            "reason": "Bukti pembayaran bank diperiksa",
        },
    )
    assert linked.status_code == 200, linked.text
    reconciliation = await ctx.create(
        "finance",
        "reconciliations",
        {
            "bank_account_id": account["bank_account_id"],
            "period_start": "2026-10-01",
            "period_end": "2026-10-31",
        },
        "workspace",
    )
    item = await ctx.create(
        "finance",
        "reconciliation-items",
        {
            "reconciliation_id": reconciliation["reconciliation_id"],
            "transaction_id": bank_transaction["transaction_id"],
            "expected_amount": "25.00",
            "actual_amount": "25.00",
        },
        "workspace",
    )
    await ctx.transition(
        "finance",
        "reconciliation-items",
        item["reconciliation_item_id"],
        "MATCHED",
        user="workspace",
    )
    await ctx.transition(
        "finance",
        "reconciliations",
        reconciliation["reconciliation_id"],
        "CLOSED",
        user="workspace",
    )
    for record_type, identity, user in (
        ("PROPERTY_PAYMENT_CERTIFICATE", certificate["payment_certificate_id"], "lead"),
        ("FINANCE_PAYABLE", payable["payable_id"], "workspace"),
    ):
        lineage = await ctx.client.get(
            f"/api/v1/business/relationships/{record_type}/{identity}", headers=ctx.headers[user]
        )
        assert lineage.status_code == 200, lineage.text
        assert len(lineage.json()["items"]) == 1
        relationship = lineage.json()["items"][0]
        assert relationship["source_id"] == certificate["payment_certificate_id"]
        assert (
            relationship["target_id"] == payable["payable_id"]
            and relationship["process_id"] == process["process_id"]
        )
    for record_type, identity in (
        ("FINANCE_PAYABLE_PAYMENT", payment["payment_id"]),
        ("FINANCE_BANK_TRANSACTION", bank_transaction["transaction_id"]),
    ):
        lineage = await ctx.client.get(
            f"/api/v1/business/relationships/{record_type}/{identity}", headers=finance_headers
        )
        assert lineage.status_code == 200 and len(lineage.json()["items"]) == 1, lineage.text
    for user in ("org", "tenant", "member"):
        denied = await ctx.client.get(
            f"/api/v1/business/relationships/FINANCE_PAYABLE/{payable['payable_id']}",
            headers=ctx.headers[user],
        )
        assert denied.status_code == 404, denied.text
    foreign_record = await ctx.client.get(
        f"/api/v1/property/payment-certificates/{certificate['payment_certificate_id']}",
        headers=finance_headers,
    )
    assert foreign_record.status_code in (403, 404)
