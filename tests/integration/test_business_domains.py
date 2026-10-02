"""Dedicated canonical APIs proved against migrated PostgreSQL, including rollback."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import OperationalError
from test_strategy_planning_e2e import (
    CONTRACTS_ROOT,
    _asyncpg_url,
    _database_url,
    _register_and_login,
)

from alos.config import Settings
from alos.main import create_app
from alos.persistence.models import AuditRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")


@dataclass
class Context:
    app: FastAPI
    client: httpx.AsyncClient
    headers: dict[str, dict[str, str]]

    async def seed_legacy_status(
        self, domain: str, table_name: str, identifier: str, identity: str, status: str
    ) -> None:
        """Restore a persisted historical fixture without granting command authority."""
        repository = getattr(self.app.state, f"{domain}_service").repository
        async with repository.factory() as session, session.begin():
            table = await repository.table(session, domain, table_name)
            result = await session.execute(
                update(table).where(table.c[identifier] == identity).values(status=status)
            )
            assert result.rowcount == 1

    async def audit_count(self, domain: str) -> int:
        repository = getattr(self.app.state, f"{domain}_service").repository
        async with repository.factory() as session:
            return int(await session.scalar(select(func.count()).select_from(AuditRecord)) or 0)

    async def create(
        self, domain: str, resource: str, values: dict[str, Any], user: str = "lead"
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/{domain}/{resource}", headers=self.headers[user], json=values
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def transition(
        self,
        domain: str,
        resource: str,
        identity: str,
        status: str,
        expected: int = 200,
        user: str = "lead",
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/{domain}/{resource}/{identity}/transition",
            headers=self.headers[user],
            json={"status": status},
        )
        assert response.status_code == expected, response.text
        return response.json()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def context() -> AsyncIterator[Context]:
    name = "alos_business_domains_test"
    admin = await asyncpg.connect(_asyncpg_url(_database_url("postgres")))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = _database_url(name)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        env={**os.environ, "DATABASE_URL": url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()
    app = create_app(
        Settings(
            _env_file=None,
            APP_ENV="development",
            DATABASE_URL=url,
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
            ENABLE_TEST_REGISTRATION=True,
        )
    )
    headers = {}
    permissions = [
        f"{domain}.{action}"
        for domain in ("sales", "marketing", "property", "finance", "legal", "hr", "it")
        for action in ("read", "write", "delete")
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        for label, tenant, org, workspace, role in (
            ("lead", "tenant_business", "org_business", "workspace_business", "DIVISION_LEAD"),
            ("member", "tenant_business", "org_business", "workspace_business", "DIVISION_MEMBER"),
            ("workspace", "tenant_business", "org_business", "workspace_else", "DIVISION_LEAD"),
            ("org", "tenant_business", "org_else", "workspace_org", "DIVISION_LEAD"),
            ("tenant", "tenant_else", "org_tenant_else", "workspace_tenant", "DIVISION_LEAD"),
            ("it", "tenant_business", "org_business", "workspace_business", "IT_ADMIN"),
            ("executive", "tenant_business", "org_business", "workspace_exec", "EXECUTIVE"),
            ("none", "tenant_business", "org_business", "workspace_business", "DIVISION_MEMBER"),
        ):
            headers[label], _actor = await _register_and_login(
                client,
                email=f"{label}@business.test",
                tenant_id=tenant,
                organization_id=org,
                workspace_id=workspace,
                roles=[role],
                permissions=[]
                if label == "none"
                else [
                    *permissions,
                    "work.read",
                    "work.write",
                    "strategy.read",
                    "approval.read",
                    "approval.request",
                    "approval.approve",
                    "approval.return",
                    "approval.reject",
                    "approval.hold",
                    "document.version",
                ],
            )
        yield Context(app, client, headers)
    await app.state.database.dispose()


@pytest.mark.parametrize(
    "domain,resource,values,identifier",
    [
        ("sales", "customers", {"customer_code": "SCOPE", "name": "Scope customer"}, "customer_id"),
        ("marketing", "channels", {"name": "Scope channel"}, "channel_id"),
        ("property", "property-units", {"unit_code": "SCOPE"}, "property_unit_id"),
        (
            "finance",
            "bank-accounts",
            {"account_name": "Scope account", "bank_name": "Internal"},
            "bank_account_id",
        ),
    ],
)
async def test_scope_authority_and_forged_fields(
    context: Context, domain: str, resource: str, values: dict[str, Any], identifier: str
) -> None:
    row = await context.create(domain, resource, values)
    for user in ("workspace", "org", "tenant"):
        response = await context.client.get(
            f"/api/v1/{domain}/{resource}/{row[identifier]}", headers=context.headers[user]
        )
        assert response.status_code == 404
        listing = await context.client.get(
            f"/api/v1/{domain}/{resource}", headers=context.headers[user]
        )
        assert listing.json()["items"] == []
        assert listing.json()["source"]["status"] == "CONNECTED_EMPTY"
        assert listing.json()["source"]["last_updated_at"] is None
        mutate = await context.client.patch(
            f"/api/v1/{domain}/{resource}/{row[identifier]}",
            headers=context.headers[user],
            json=values,
        )
        assert mutate.status_code == 404
    for user in ("it", "executive", "none"):
        response = await context.client.post(
            f"/api/v1/{domain}/{resource}", headers=context.headers[user], json=values
        )
        assert response.status_code == 403
    for field in ("tenant_id", "organization_id", "workspace_id", "actor_id", "status", identifier):
        response = await context.client.post(
            f"/api/v1/{domain}/{resource}",
            headers=context.headers["lead"],
            json={**values, field: "forged"},
        )
        assert response.status_code == 422
    generic = await context.client.post(
        f"/api/v1/domains/{domain}/{resource.replace('-', '_')}",
        headers=context.headers["lead"],
        json=values,
    )
    assert generic.status_code == 409


async def test_sales_customer_lead_pipeline_booking_and_closing(context: Context) -> None:
    customer = await context.create(
        "sales", "customers", {"customer_code": "PIPE", "name": "Buyer"}
    )
    cid = customer["customer_id"]
    lead = await context.create(
        "sales", "leads", {"customer_id": cid, "source": "Direct", "interest": "Unit"}
    )
    await context.transition("sales", "leads", lead["lead_id"], "FOLLOW_UP")
    await context.transition("sales", "leads", lead["lead_id"], "QUALIFIED")
    await context.transition("sales", "leads", lead["lead_id"], "NEW", 409)
    opportunity = await context.create(
        "sales",
        "opportunities",
        {
            "customer_id": cid,
            "lead_id": lead["lead_id"],
            "name": "Purchase",
            "estimated_value": "123456789012.34",
            "probability": "25.00",
        },
    )
    assert opportunity["estimated_value"] == "123456789012.34"
    for stage in ("Qualified", "Survey", "Booking"):
        response = await context.client.post(
            f"/api/v1/sales/opportunities/{opportunity['opportunity_id']}/pipeline",
            headers=context.headers["lead"],
            json={"stage": stage},
        )
        assert response.status_code == 200, response.text
    await context.transition("sales", "opportunities", opportunity["opportunity_id"], "WON", 409)
    unit = await context.create("property", "property-units", {"unit_code": "BOOK"})
    uid = unit["property_unit_id"]
    booking = await context.create(
        "sales",
        "bookings",
        {
            "customer_id": cid,
            "property_unit_id": uid,
            "booking_date": "2027-01-02",
            "amount": "1000.00",
        },
    )
    await context.transition("sales", "bookings", booking["booking_id"], "CONFIRMED", 409)
    duplicate = await context.client.post(
        "/api/v1/sales/bookings",
        headers=context.headers["lead"],
        json={"customer_id": cid, "property_unit_id": uid, "booking_date": "2027-01-02"},
    )
    assert duplicate.status_code == 409
    closing = await context.create(
        "sales",
        "closings",
        {
            "booking_id": booking["booking_id"],
            "customer_id": cid,
            "property_unit_id": uid,
            "closing_date": "2027-01-03",
        },
    )
    await context.transition("sales", "closings", closing["closing_id"], "COMPLETED", 409)
    await context.transition("sales", "closings", closing["closing_id"], "CANCELLED")
    await context.transition("sales", "bookings", booking["booking_id"], "CANCELLED")
    for values in (
        {"customer_id": "forged", "name": "Invalid"},
        {"customer_id": cid, "lead_id": "forged", "name": "Invalid"},
    ):
        response = await context.client.post(
            "/api/v1/sales/opportunities", headers=context.headers["lead"], json=values
        )
        assert response.status_code == 404
    forged = await context.client.post(
        "/api/v1/sales/bookings",
        headers=context.headers["lead"],
        json={"customer_id": cid, "property_unit_id": "forged", "booking_date": "2027-01-01"},
    )
    assert forged.status_code == 404


async def test_marketing_campaign_content_and_explicit_attribution(context: Context) -> None:
    campaign = await context.create(
        "marketing",
        "campaigns",
        {
            "name": "Recorded",
            "budget": "100.25",
            "start_date": "2027-01-01",
            "end_date": "2027-02-01",
        },
    )
    await context.transition("marketing", "campaigns", campaign["campaign_id"], "ACTIVE")
    content = await context.create(
        "marketing",
        "contents",
        {"campaign_id": campaign["campaign_id"], "title": "Content", "content_type": "ARTICLE"},
    )
    published = await context.transition(
        "marketing", "contents", content["content_id"], "PUBLISHED"
    )
    assert published["published_at"] is not None
    patch = await context.client.patch(
        f"/api/v1/marketing/contents/{content['content_id']}",
        headers=context.headers["lead"],
        json={"title": "Overwrite"},
    )
    assert patch.status_code == 409
    customer = await context.create(
        "sales", "customers", {"customer_code": "ATTR", "name": "Attribution"}
    )
    attribution = await context.create(
        "marketing",
        "attributions",
        {
            "customer_id": customer["customer_id"],
            "campaign_id": campaign["campaign_id"],
            "occurred_at": "2020-01-01T00:00:00Z",
            "touch_type": "CONTACT",
        },
    )
    assert attribution["touch_type"] == "CONTACT"
    invalid = await context.client.post(
        "/api/v1/marketing/attributions",
        headers=context.headers["lead"],
        json={"occurred_at": "2020-01-01T00:00:00Z"},
    )
    assert invalid.status_code == 409
    await context.transition("marketing", "campaigns", campaign["campaign_id"], "COMPLETED")


async def test_property_project_execution_quality_and_governance(context: Context) -> None:
    project = await context.client.post(
        "/api/v1/projects",
        headers=context.headers["lead"],
        json={"code": "CONSTRUCTION", "name": "Construction"},
    )
    assert project.status_code == 201, project.text
    pid = project.json()["project_id"]
    package = await context.create(
        "property",
        "construction-packages",
        {"project_id": pid, "package_code": "P1", "name": "Package"},
    )
    await context.transition(
        "property", "construction-packages", package["construction_package_id"], "IN_PROGRESS"
    )
    update = await context.create(
        "property",
        "construction-updates",
        {
            "construction_package_id": package["construction_package_id"],
            "update_date": "2027-01-01",
            "progress_percent": "15.25",
            "summary": "Measured",
        },
    )
    assert update["progress_percent"] == "15.25"
    invalid = await context.client.post(
        "/api/v1/property/construction-updates",
        headers=context.headers["lead"],
        json={
            "construction_package_id": package["construction_package_id"],
            "update_date": "2027-01-01",
            "progress_percent": "101",
        },
    )
    assert invalid.status_code in {409, 422}
    inspection = await context.create(
        "property",
        "quality-inspections",
        {
            "project_id": pid,
            "inspection_type": "STRUCTURE",
            "inspection_date": "2027-01-01",
            "result": "FAIL",
        },
    )
    ncr = await context.create(
        "property",
        "quality-ncrs",
        {
            "project_id": pid,
            "inspection_id": inspection["inspection_id"],
            "title": "Recorded",
            "severity": "CRITICAL",
        },
    )
    await context.transition("property", "quality-ncrs", ncr["ncr_id"], "IN_PROGRESS")
    await context.transition("property", "quality-ncrs", ncr["ncr_id"], "CLOSED")
    safety = await context.create(
        "property",
        "safety-incidents",
        {
            "project_id": pid,
            "incident_date": "2027-01-01",
            "severity": "LOW",
            "description": "critical word does not change supplied severity",
        },
    )
    assert safety["severity"] == "LOW"
    order = await context.create(
        "property",
        "change-orders",
        {
            "project_id": pid,
            "change_number": "C1",
            "description": "Change",
            "amount_delta": "-10.25",
        },
    )
    await context.transition("property", "change-orders", order["change_order_id"], "SUBMITTED")
    await context.transition("property", "change-orders", order["change_order_id"], "APPROVED", 409)
    certificate = await context.create(
        "property",
        "payment-certificates",
        {"project_id": pid, "certificate_number": "PC1", "period": "2027-01", "amount": "10.25"},
    )
    pcid = certificate["payment_certificate_id"]
    await context.transition("property", "payment-certificates", pcid, "SUBMITTED")
    for user in ("member", "lead"):
        await context.transition("property", "payment-certificates", pcid, "APPROVED", 409, user)
        await context.transition(
            "property", "change-orders", order["change_order_id"], "APPROVED", 409, user
        )
    handover = await context.create(
        "property",
        "project-handovers",
        {"project_id": pid, "handover_type": "INTERNAL", "handover_date": "2027-02-01"},
    )
    await context.transition("property", "project-handovers", handover["handover_id"], "COMPLETED")
    invalid = await context.client.post(
        "/api/v1/property/project-milestones",
        headers=context.headers["workspace"],
        json={"project_id": pid, "name": "Forged"},
    )
    assert invalid.status_code == 404


@pytest.mark.parametrize(
    "resource,payments,identifier",
    [
        ("receivables", "receivable-payments", "receivable_id"),
        ("payables", "payable-payments", "payable_id"),
    ],
)
async def test_finance_exact_payments_duplicate_overpayment_and_history(
    context: Context, resource: str, payments: str, identifier: str
) -> None:
    invoice = await context.create(
        "finance", resource, {"reference": uuid4().hex, "amount": "100.01"}
    )
    assert invoice["outstanding_amount"] == "100.01"
    values = {
        identifier: invoice[identifier],
        "payment_date": "2027-03-01",
        "amount": "40.01",
        "reference": "PAY-1",
    }
    await context.create("finance", payments, values)
    duplicate = await context.client.post(
        f"/api/v1/finance/{payments}", headers=context.headers["lead"], json=values
    )
    assert duplicate.status_code == 409
    for amount in ("60.01", "0", "-1", "NaN", "Infinity", "1.001", 1.1):
        invalid = await context.client.post(
            f"/api/v1/finance/{payments}",
            headers=context.headers["lead"],
            json={**values, "reference": uuid4().hex, "amount": amount},
        )
        assert invalid.status_code in {409, 422}
    await context.create("finance", payments, {**values, "reference": "PAY-2", "amount": "60.00"})
    read = await context.client.get(
        f"/api/v1/finance/{resource}/{invoice[identifier]}", headers=context.headers["lead"]
    )
    assert read.json()["status"] == "PAID"
    assert read.json()["outstanding_amount"] == "0.00"
    forged = await context.client.post(
        f"/api/v1/finance/{payments}", headers=context.headers["workspace"], json=values
    )
    assert forged.status_code == 404


async def test_finance_budget_reconciliation_and_month_close(context: Context) -> None:
    budget = await context.create("finance", "budgets", {"name": "Budget", "fiscal_year": 2027})
    bid = budget["budget_id"]
    await context.create(
        "finance",
        "budget-lines",
        {"budget_id": bid, "account_code": "OPS", "period": "2027-04", "planned_amount": "250.00"},
    )
    await context.transition("finance", "budgets", bid, "UNDER_REVIEW")
    member_read = await context.client.get(
        f"/api/v1/finance/budgets/{bid}", headers=context.headers["member"]
    )
    assert member_read.status_code == 200
    assert "APPROVED" not in member_read.json()["allowed_transitions"]
    await context.transition("finance", "budgets", bid, "APPROVED", 409, "member")
    await context.transition("finance", "budgets", bid, "APPROVED", 409)
    await context.transition("finance", "budgets", bid, "DRAFT")
    account = await context.create(
        "finance",
        "bank-accounts",
        {"account_name": "Ledger", "bank_name": "Internal", "currency": "USD"},
    )
    aid = account["bank_account_id"]
    txn = await context.create(
        "finance",
        "bank-transactions",
        {
            "bank_account_id": aid,
            "transaction_date": "2027-05-10",
            "direction": "IN",
            "amount": "10.25",
            "currency": "USD",
        },
    )
    mismatch = await context.client.post(
        "/api/v1/finance/bank-transactions",
        headers=context.headers["lead"],
        json={
            "bank_account_id": aid,
            "transaction_date": "2027-05-10",
            "direction": "IN",
            "amount": "10",
            "currency": "IDR",
        },
    )
    assert mismatch.status_code == 409
    rec = await context.create(
        "finance",
        "reconciliations",
        {"bank_account_id": aid, "period_start": "2027-05-01", "period_end": "2027-05-31"},
    )
    rid = rec["reconciliation_id"]
    item = await context.create(
        "finance",
        "reconciliation-items",
        {
            "reconciliation_id": rid,
            "transaction_id": txn["transaction_id"],
            "expected_amount": "10.25",
            "actual_amount": "10.25",
        },
    )
    await context.transition("finance", "reconciliations", rid, "CLOSED", 409)
    await context.transition(
        "finance", "reconciliation-items", item["reconciliation_item_id"], "MATCHED"
    )
    await context.transition("finance", "reconciliations", rid, "CLOSED")
    month = await context.create("finance", "month-closes", {"period": "2027-05"})
    mid = month["month_close_id"]
    check = await context.create(
        "finance",
        "month-close-items",
        {"month_close_id": mid, "item_type": "INTERNAL_REVIEW", "notes": "Recorded checklist"},
    )
    await context.transition("finance", "month-closes", mid, "CLOSED", 409)
    await context.transition(
        "finance", "month-close-items", check["month_close_item_id"], "COMPLETED"
    )
    await context.transition("finance", "month-closes", mid, "CLOSED", 409)
    await context.seed_legacy_status("finance", "month_closes", "month_close_id", mid, "CLOSED")
    blocked = await context.client.post(
        "/api/v1/finance/bank-transactions",
        headers=context.headers["lead"],
        json={
            "bank_account_id": aid,
            "transaction_date": "2027-05-11",
            "direction": "IN",
            "amount": "1.00",
            "currency": "USD",
        },
    )
    assert blocked.status_code == 409
    tax = await context.create(
        "finance",
        "tax-obligations",
        {"tax_type": "INTERNAL", "period": "2027-06", "amount": "100.00", "due_date": "2027-07-10"},
    )
    await context.transition("finance", "tax-obligations", tax["tax_obligation_id"], "CLOSED", 409)


@pytest.mark.parametrize("user", ["member", "lead"])
async def test_pricing_activation_requires_unavailable_canonical_authority(
    context: Context, user: str
) -> None:
    unit = await context.create("property", "property-units", {"unit_code": uuid4().hex})
    pricing = await context.create("sales", "pricings", {"name": "Prepared pricing"}, user)
    identity = pricing["pricing_id"]
    item = await context.create(
        "sales",
        "pricing-items",
        {"pricing_id": identity, "property_unit_id": unit["property_unit_id"], "price": "100.00"},
        user,
    )
    assert pricing["status"] == "DRAFT" and pricing["allowed_transitions"] == []
    for resource, record_id, values in (
        ("pricings", identity, {"name": "Edited draft"}),
        ("pricing-items", item["pricing_item_id"], {"price": "125.00"}),
    ):
        edited = await context.client.patch(
            f"/api/v1/sales/{resource}/{record_id}", headers=context.headers[user], json=values
        )
        assert edited.status_code == 200, edited.text
    audit_before = await context.audit_count("sales")
    denied = await context.transition("sales", "pricings", identity, "ACTIVE", 409, user)
    assert denied["code"] == "BUSINESS_STATE_CONFLICT"
    read = await context.client.get(
        f"/api/v1/sales/pricings/{identity}", headers=context.headers[user]
    )
    assert read.status_code == 200 and read.json()["status"] == "DRAFT"
    assert read.json()["allowed_transitions"] == []
    assert await context.audit_count("sales") == audit_before
    await context.seed_legacy_status("sales", "pricings", "pricing_id", identity, "ACTIVE")
    historical = await context.client.get(
        f"/api/v1/sales/pricings/{identity}", headers=context.headers[user]
    )
    assert historical.status_code == 200 and historical.json()["status"] == "ACTIVE"
    assert historical.json()["updated_at"] == read.json()["updated_at"]
    assert "ACTIVE" not in historical.json()["allowed_transitions"]
    immutable = await context.client.patch(
        f"/api/v1/sales/pricings/{identity}",
        headers=context.headers[user],
        json={"name": "Rewrite"},
    )
    assert immutable.status_code == 409


@pytest.mark.parametrize("user", ["member", "lead"])
async def test_budget_preparation_does_not_grant_material_authority(
    context: Context, user: str
) -> None:
    budget = await context.create(
        "finance", "budgets", {"name": f"Prepared {user}", "fiscal_year": 2028}, user
    )
    identity = budget["budget_id"]
    await context.create(
        "finance",
        "budget-lines",
        {
            "budget_id": identity,
            "account_code": "OPS",
            "period": "2028-01",
            "planned_amount": "1.00",
        },
        user,
    )
    reviewing = await context.transition("finance", "budgets", identity, "UNDER_REVIEW", user=user)
    assert reviewing["allowed_transitions"] == ["DRAFT"]
    audit_before = await context.audit_count("finance")
    await context.transition("finance", "budgets", identity, "APPROVED", 409, user)
    assert await context.audit_count("finance") == audit_before
    prepared = await context.transition("finance", "budgets", identity, "DRAFT", user=user)
    assert prepared["allowed_transitions"] == ["UNDER_REVIEW"]


@pytest.mark.parametrize("user", ["member", "lead"])
@pytest.mark.parametrize(
    "status,target", [("APPROVED", "ACTIVE"), ("ACTIVE", "CLOSED"), ("CLOSED", "DRAFT")]
)
async def test_historical_budget_is_readable_without_material_commands(
    context: Context, user: str, status: str, target: str
) -> None:
    budget = await context.create(
        "finance", "budgets", {"name": f"Historical {status} {user}", "fiscal_year": 2028}
    )
    identity = budget["budget_id"]
    await context.seed_legacy_status("finance", "budgets", "budget_id", identity, status)
    audit_before = await context.audit_count("finance")
    path = f"/api/v1/finance/budgets/{identity}"
    read = await context.client.get(path, headers=context.headers[user])
    assert read.status_code == 200
    assert read.json()["status"] == status and read.json()["allowed_transitions"] == []
    assert read.json()["updated_at"] == budget["updated_at"]
    listing = await context.client.get("/api/v1/finance/budgets", headers=context.headers[user])
    assert any(
        record["budget_id"] == identity and record["status"] == status
        for record in listing.json()["items"]
    )
    await context.transition("finance", "budgets", identity, target, 409, user)
    edited = await context.client.patch(
        path, headers=context.headers[user], json={"name": "Rewrite"}
    )
    assert edited.status_code == 409
    assert await context.audit_count("finance") == audit_before


@pytest.mark.parametrize("user,period", [("member", "2028-02"), ("lead", "2028-03")])
async def test_month_close_unavailable_and_historical_period_stays_closed(
    context: Context, user: str, period: str
) -> None:
    month = await context.create("finance", "month-closes", {"period": period}, user)
    identity = month["month_close_id"]
    item = await context.create(
        "finance",
        "month-close-items",
        {"month_close_id": identity, "item_type": "INTERNAL_REVIEW"},
        user,
    )
    await context.transition(
        "finance", "month-close-items", item["month_close_item_id"], "COMPLETED", user=user
    )
    audit_before = await context.audit_count("finance")
    assert month["status"] == "OPEN" and month["allowed_transitions"] == []
    await context.transition("finance", "month-closes", identity, "CLOSED", 409, user)
    read = await context.client.get(
        f"/api/v1/finance/month-closes/{identity}", headers=context.headers[user]
    )
    assert read.json() == month
    assert await context.audit_count("finance") == audit_before
    await context.seed_legacy_status(
        "finance", "month_closes", "month_close_id", identity, "CLOSED"
    )
    historical = await context.client.get(
        f"/api/v1/finance/month-closes/{identity}", headers=context.headers[user]
    )
    assert historical.status_code == 200 and historical.json()["status"] == "CLOSED"
    assert historical.json()["allowed_transitions"] == []
    assert historical.json()["updated_at"] == month["updated_at"]
    assert historical.json()["closed_at"] is None
    await context.transition("finance", "month-closes", identity, "OPEN", 409, user)
    account = await context.create(
        "finance",
        "bank-accounts",
        {"account_name": "History", "bank_name": "Internal", "currency": "USD"},
        user,
    )
    for resource, values in (
        (
            "bank-transactions",
            {
                "bank_account_id": account["bank_account_id"],
                "transaction_date": f"{period}-10",
                "direction": "IN",
                "amount": "1.00",
                "currency": "USD",
            },
        ),
        ("month-close-items", {"month_close_id": identity, "item_type": "NEW_CHECK"}),
        ("month-closes", {"period": period}),
    ):
        denied = await context.client.post(
            f"/api/v1/finance/{resource}", headers=context.headers[user], json=values
        )
        assert denied.status_code == 409, denied.text


async def test_hidden_transition_is_enforced_before_owner_business_rule(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await context.create(
        "finance",
        "bank-accounts",
        {"account_name": "Authority", "bank_name": "Internal", "currency": "USD"},
    )
    rec = await context.create(
        "finance",
        "reconciliations",
        {
            "bank_account_id": account["bank_account_id"],
            "period_start": "2028-04-01",
            "period_end": "2028-04-30",
        },
    )
    transaction = await context.create(
        "finance",
        "bank-transactions",
        {
            "bank_account_id": account["bank_account_id"],
            "transaction_date": "2028-04-10",
            "direction": "IN",
            "amount": "1.00",
            "currency": "USD",
        },
    )
    item = await context.create(
        "finance",
        "reconciliation-items",
        {
            "reconciliation_id": rec["reconciliation_id"],
            "transaction_id": transaction["transaction_id"],
            "expected_amount": "1.00",
            "actual_amount": "1.00",
        },
    )
    await context.transition(
        "finance", "reconciliation-items", item["reconciliation_item_id"], "MATCHED"
    )
    path = f"/api/v1/finance/reconciliations/{rec['reconciliation_id']}"
    projection = await context.client.get(path, headers=context.headers["member"])
    assert projection.json()["allowed_transitions"] == []
    service = context.app.state.finance_service
    original = service._rule
    rule_called = False

    async def observe_rule(*args: Any, **kwargs: Any) -> None:
        nonlocal rule_called
        rule_called = True
        await original(*args, **kwargs)

    monkeypatch.setattr(service, "_rule", observe_rule)
    audit_before = await context.audit_count("finance")
    denied = await context.transition(
        "finance", "reconciliations", rec["reconciliation_id"], "CLOSED", 403, "member"
    )
    assert denied["code"] == "BUSINESS_TRANSITION_DENIED"
    assert not rule_called
    assert await context.audit_count("finance") == audit_before
    read = await context.client.get(path, headers=context.headers["member"])
    assert read.json() == projection.json()
    closed = await context.transition(
        "finance", "reconciliations", rec["reconciliation_id"], "CLOSED"
    )
    assert closed["status"] == "CLOSED" and rule_called


@pytest.mark.parametrize(
    "domain,resource,values",
    [
        ("sales", "customers", {"customer_code": "ROLLBACK", "name": "Rollback"}),
        ("marketing", "campaigns", {"name": "Rollback"}),
        ("property", "property-units", {"unit_code": "ROLLBACK"}),
        ("finance", "receivables", {"reference": "ROLLBACK", "amount": "100.00"}),
    ],
)
async def test_real_state_and_audit_rollback(
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
    domain: str,
    resource: str,
    values: dict[str, Any],
) -> None:
    repository = getattr(context.app.state, f"{domain}_service").repository
    original = repository.audit.append_in_session

    async def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        await original(*_args, **_kwargs)
        raise RuntimeError("Injected audit failure")

    before = await context.client.get(
        f"/api/v1/{domain}/{resource}", headers=context.headers["lead"]
    )
    async with repository.factory() as session:
        audit_before = await session.scalar(select(func.count()).select_from(AuditRecord))
    monkeypatch.setattr(repository.audit, "append_in_session", fail_audit)
    response = await context.client.post(
        f"/api/v1/{domain}/{resource}", headers=context.headers["lead"], json=values
    )
    assert response.status_code == 500
    monkeypatch.setattr(repository.audit, "append_in_session", original)
    after = await context.client.get(
        f"/api/v1/{domain}/{resource}", headers=context.headers["lead"]
    )
    assert before.json()["total"] == after.json()["total"]
    async with repository.factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditRecord)) == audit_before
    await context.create(domain, resource, values)


async def test_executive_real_business_connection_status_and_source_timestamps(
    context: Context,
) -> None:
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["executive"]
    )
    assert response.status_code == 200, response.text
    data = response.json()
    domains = {row["domain"]: row for row in data["domains"]}
    for name in ("SALES", "PROPERTY", "FINANCE"):
        assert domains[name]["status"] == "CONNECTED"
        assert all(source["authoritative"] for source in domains[name]["sources"])
        assert domains[name]["last_verified_at"] is not None
    for name in ("LEGAL", "HR", "IT"):
        assert domains[name]["status"] == "CONNECTED_EMPTY"
        assert domains[name]["last_verified_at"] is None
    forbidden = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["it"]
    )
    assert forbidden.status_code == 403


async def test_sales_unit_read_port_requires_real_shared_project_visibility(
    context: Context,
) -> None:
    project = await context.client.post(
        "/api/v1/projects",
        headers=context.headers["workspace"],
        json={"code": "SHARED-UNIT", "name": "Visible project"},
    )
    assert project.status_code == 201
    pid = project.json()["project_id"]
    unit = await context.create(
        "property", "property-units", {"unit_code": "SHARED-UNIT", "project_id": pid}, "workspace"
    )
    hidden = await context.client.get(
        "/api/v1/sales/property-units", headers=context.headers["lead"]
    )
    assert hidden.status_code == 200, hidden.text
    assert unit["property_unit_id"] not in {
        row["property_unit_id"] for row in hidden.json()["items"]
    }
    repository = context.app.state.property_service.repository
    async with repository.factory() as session, session.begin():
        links = await repository.table(session, "core", "project_workspaces")
        await session.execute(
            insert(links).values(project_id=pid, workspace_id="workspace_business")
        )
    visible = await context.client.get(
        "/api/v1/sales/property-units", headers=context.headers["lead"]
    )
    projected = next(
        row
        for row in visible.json()["items"]
        if row["property_unit_id"] == unit["property_unit_id"]
    )
    assert set(projected) == {"property_unit_id", "unit_code", "status", "project_id"}
    customer = await context.create(
        "sales", "customers", {"customer_code": "SHARED", "name": "Buyer"}
    )
    await context.create(
        "sales",
        "bookings",
        {
            "customer_id": customer["customer_id"],
            "property_unit_id": unit["property_unit_id"],
            "booking_date": "2027-01-01",
        },
    )
    still_hidden = await context.client.get(
        f"/api/v1/property/property-units/{unit['property_unit_id']}",
        headers=context.headers["lead"],
    )
    assert still_hidden.status_code == 404


async def test_concurrent_payments_serialize_exact_outstanding(context: Context) -> None:
    invoice = await context.create(
        "finance", "receivables", {"reference": "CONCURRENT", "amount": "100.00"}
    )

    async def pay(reference: str) -> httpx.Response:
        return await context.client.post(
            "/api/v1/finance/receivable-payments",
            headers=context.headers["lead"],
            json={
                "receivable_id": invoice["receivable_id"],
                "reference": reference,
                "payment_date": "2027-08-01",
                "amount": "60.00",
            },
        )

    results = await asyncio.gather(pay("C1"), pay("C2"))
    assert sorted(result.status_code for result in results) == [201, 409]
    read = await context.client.get(
        f"/api/v1/finance/receivables/{invoice['receivable_id']}", headers=context.headers["lead"]
    )
    assert read.json()["outstanding_amount"] == "40.00"


async def test_payment_audit_failure_rolls_back_parent_and_history(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice = await context.create(
        "finance", "payables", {"reference": "PAYMENT-ROLLBACK", "amount": "75.25"}
    )
    repository = context.app.state.finance_service.repository
    original = repository.audit.append_in_session

    async def fail_after_insert(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)
        raise RuntimeError("Injected audit failure after persistence")

    before = await context.client.get(
        "/api/v1/finance/payable-payments", headers=context.headers["lead"]
    )
    async with repository.factory() as session:
        audit_before = await session.scalar(select(func.count()).select_from(AuditRecord))
    values = {
        "payable_id": invoice["payable_id"],
        "payment_date": "2027-08-01",
        "amount": "25.25",
        "reference": "ROLLBACK-1",
    }
    monkeypatch.setattr(repository.audit, "append_in_session", fail_after_insert)
    failed = await context.client.post(
        "/api/v1/finance/payable-payments", headers=context.headers["lead"], json=values
    )
    assert failed.status_code == 500
    monkeypatch.setattr(repository.audit, "append_in_session", original)
    read = await context.client.get(
        f"/api/v1/finance/payables/{invoice['payable_id']}", headers=context.headers["lead"]
    )
    after = await context.client.get(
        "/api/v1/finance/payable-payments", headers=context.headers["lead"]
    )
    assert read.json()["outstanding_amount"] == "75.25"
    assert after.json()["total"] == before.json()["total"]
    async with repository.factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditRecord)) == audit_before
    await context.create("finance", "payable-payments", values)


async def test_finance_reconciliation_scope_and_closed_period_history(context: Context) -> None:
    account = await context.create(
        "finance", "bank-accounts", {"account_name": "Closed period", "bank_name": "Internal"}
    )
    other = await context.create(
        "finance", "bank-accounts", {"account_name": "Other account", "bank_name": "Internal"}
    )
    transaction = await context.create(
        "finance",
        "bank-transactions",
        {
            "bank_account_id": other["bank_account_id"],
            "transaction_date": "2027-09-01",
            "direction": "OUT",
            "amount": "20.25",
        },
    )
    reconciliation = await context.create(
        "finance",
        "reconciliations",
        {
            "bank_account_id": account["bank_account_id"],
            "period_start": "2027-09-01",
            "period_end": "2027-09-30",
        },
    )
    mismatch = await context.client.post(
        "/api/v1/finance/reconciliation-items",
        headers=context.headers["lead"],
        json={
            "reconciliation_id": reconciliation["reconciliation_id"],
            "transaction_id": transaction["transaction_id"],
            "expected_amount": "20.25",
            "actual_amount": "20.25",
        },
    )
    assert mismatch.status_code == 409
    wrong_period = await context.client.patch(
        f"/api/v1/finance/reconciliations/{reconciliation['reconciliation_id']}",
        headers=context.headers["lead"],
        json={"period_start": "2027-08-01"},
    )
    assert wrong_period.status_code == 405
    month = await context.create("finance", "month-closes", {"period": "2027-09"})
    item = await context.create(
        "finance",
        "month-close-items",
        {"month_close_id": month["month_close_id"], "item_type": "RECONCILIATION"},
    )
    await context.transition(
        "finance", "month-close-items", item["month_close_item_id"], "COMPLETED"
    )
    await context.transition("finance", "month-closes", month["month_close_id"], "CLOSED", 409)
    await context.transition(
        "finance", "reconciliations", reconciliation["reconciliation_id"], "CLOSED", 409
    )


@pytest.mark.parametrize("domain", ["sales", "marketing", "property", "finance"])
async def test_business_retrieval_error_is_not_empty_and_executive_is_partial(
    context: Context, domain: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = getattr(context.app.state, f"{domain}_service")

    async def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OperationalError("hidden SQL", {}, RuntimeError("hidden source details"))

    monkeypatch.setattr(service, "overview", fail)
    overview = await context.client.get(
        f"/api/v1/{domain}/overview", headers=context.headers["lead"]
    )
    assert overview.status_code == 503
    assert "hidden" not in overview.text
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["executive"]
    )
    assert response.status_code == 200
    key = "SALES" if domain == "marketing" else domain.upper()
    data = next(item for item in response.json()["domains"] if item["domain"] == key)
    assert data["status"] == "ERROR"
    failed = next(source for source in data["sources"] if source["source"] == domain)
    assert failed["status"] == "ERROR" and failed["authoritative"] is True
    assert failed["last_updated_at"] is None
    assert response.json()["strategy"]["status"] != "ERROR"
    assert response.json()["shared_work"]["status"] != "ERROR"


async def test_executive_business_workspace_visibility_is_not_implicitly_company(
    context: Context,
) -> None:
    repository = context.app.state.finance_service.repository
    async with repository.factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE core.workspace_memberships SET data_scope='WORKSPACE' "
                "WHERE workspace_id='workspace_exec' AND tenant_id='tenant_business' "
                "AND organization_id='org_business'"
            )
        )
    try:
        response = await context.client.get(
            "/api/v1/executive/overview", headers=context.headers["executive"]
        )
        assert response.status_code == 200
        for domain in response.json()["domains"]:
            if domain["domain"] in {"SALES", "PROPERTY", "FINANCE"}:
                assert domain["status"] == "CONNECTED_EMPTY"
                assert all(source["last_updated_at"] is None for source in domain["sources"])
    finally:
        async with repository.factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE core.workspace_memberships SET data_scope='COMPANY' "
                    "WHERE workspace_id='workspace_exec' AND tenant_id='tenant_business' "
                    "AND organization_id='org_business'"
                )
            )


async def test_business_contract_unavailability_is_fail_closed_even_for_executive(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = context.app.state.finance_service.repository
    assert repository.contracts is not None
    original = repository.contracts.validate

    def unavailable(schema_id: str, payload: Any) -> Any:
        if schema_id.endswith("finance/finance-contracts.schema.json"):
            raise ValueError("Canonical Finance contract unavailable")
        return original(schema_id, payload)

    monkeypatch.setattr(repository.contracts, "validate", unavailable)
    for path, user in (
        ("/api/v1/finance/overview", "lead"),
        ("/api/v1/executive/overview", "executive"),
    ):
        response = await context.client.get(path, headers=context.headers[user])
        assert response.status_code == 503
        assert response.json()["code"] == "CONTRACTS_UNAVAILABLE"


@pytest.mark.parametrize(
    "domain,resource,identifier,values,patch",
    [
        (
            "sales",
            "customers",
            "customer_id",
            {"customer_code": "LEGACY", "name": "Legacy"},
            {"name": "Changed"},
        ),
        ("marketing", "channels", "channel_id", {"name": "Legacy"}, {"name": "Changed"}),
        (
            "property",
            "property-units",
            "property_unit_id",
            {"unit_code": "LEGACY"},
            {"unit_name": "Changed"},
        ),
        (
            "finance",
            "bank-accounts",
            "bank_account_id",
            {"account_name": "Legacy", "bank_name": "Internal"},
            {"account_name": "Changed"},
        ),
    ],
)
async def test_unrecognized_persisted_lifecycle_remains_readable_but_cannot_mutate(
    context: Context,
    domain: str,
    resource: str,
    identifier: str,
    values: dict[str, Any],
    patch: dict[str, Any],
) -> None:
    row = await context.create(domain, resource, values)
    repository = getattr(context.app.state, f"{domain}_service").repository
    async with repository.factory() as session, session.begin():
        table = await repository.table(session, domain, resource.replace("-", "_"))
        await session.execute(
            table.update()
            .where(table.c[identifier] == row[identifier])
            .values(status="LEGACY_UNKNOWN")
        )
    path = f"/api/v1/{domain}/{resource}/{row[identifier]}"
    read = await context.client.get(path, headers=context.headers["lead"])
    assert read.status_code == 200
    assert read.json()["status"] == "LEGACY_UNKNOWN"
    assert read.json()["allowed_transitions"] == []
    mutation = await context.client.patch(path, headers=context.headers["lead"], json=patch)
    assert mutation.status_code == 409
