"""Sales commands produce evidenced actuals, verification and Executive performance."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from test_business_domains import Context
from test_material_approvals import decide, execute, request
from test_strategy_planning_e2e import StrategyContext, _preview_candidate, _register_and_login
from test_strategy_planning_e2e import strategy_context as migrated_strategy

pytestmark = pytest.mark.asyncio(loop_scope="module")
strategy_context = migrated_strategy


async def test_closing_actual_reaches_verified_executive_performance(
    strategy_context: StrategyContext,
) -> None:
    ctx = strategy_context
    workspace = "workspace_strategy_sales_e2e"
    headers: dict[str, dict[str, str]] = {}
    permissions = [
        "strategy.read",
        "strategy.division.manage",
        "strategy.observation.verify",
        "sales.read",
        "sales.write",
        "finance.read",
        "finance.write",
        "hr.read",
        "hr.write",
        "it.read",
        "it.write",
        "property.read",
        "property.write",
        "work.read",
        "work.write",
        "approval.read",
        "approval.request",
        "approval.approve",
    ]
    for label, role in (("lead", "DIVISION_LEAD"), ("member", "DIVISION_MEMBER")):
        headers[label], _ = await _register_and_login(
            ctx.client,
            email=f"metrics-{label}@e2e.local",
            tenant_id="tenant_strategy_e2e",
            organization_id="org_strategy_e2e",
            workspace_id=workspace,
            roles=[role],
            permissions=permissions,
        )
    preview, candidate = await _preview_candidate(
        ctx,
        "business.closing",
        workspace_id=workspace,
        scope_type="DIVISION",
    )
    accepted = await ctx.client.post(
        f"/api/v1/strategy/cascade-runs/{preview['cascade_run_id']}/accept",
        headers=ctx.executive_headers,
        json={"derived_targets": [candidate]},
    )
    assert accepted.status_code == 200, accepted.text
    for command in ("submit", "approve", "activate"):
        result = await ctx.client.post(
            f"/api/v1/strategy/plans/{candidate['plan_ref']['id']}/{command}",
            headers=ctx.executive_headers,
        )
        assert result.status_code == 200, result.text
    business = Context(ctx.app, ctx.client, headers)
    customer = await business.create(
        "sales", "customers", {"customer_code": "METRIC", "name": "Buyer"}
    )
    unit = await business.create("property", "property-units", {"unit_code": "METRIC"})
    booking = await business.create(
        "sales",
        "bookings",
        {
            "customer_id": customer["customer_id"],
            "property_unit_id": unit["property_unit_id"],
            "booking_date": "2027-03-01",
            "amount": "125.00",
        },
    )
    decision = await request(business, "SALES_BOOKING", booking["booking_id"], "CONFIRM_BOOKING")
    await decide(business, decision)
    await execute(business, "sales", "bookings", booking["booking_id"], "CONFIRMED", decision)
    closing = await business.create(
        "sales",
        "closings",
        {
            "booking_id": booking["booking_id"],
            "closing_date": "2027-03-05",
            "amount": "125.00",
            "customer_id": customer["customer_id"],
            "property_unit_id": unit["property_unit_id"],
        },
    )
    decision = await request(business, "SALES_CLOSING", closing["closing_id"], "COMPLETE_CLOSING")
    await decide(business, decision)
    await execute(business, "sales", "closings", closing["closing_id"], "COMPLETED", decision)
    property_result = await ctx.client.get(
        f"/api/v1/property/property-units/{unit['property_unit_id']}",
        headers=headers["lead"],
    )
    assert property_result.json()["status"] == "SOLD"
    target_id = candidate["target_id"]
    binding = await ctx.client.post(
        f"/api/v1/business/targets/{target_id}/source-binding",
        headers=headers["lead"],
        json={
            "target_version": 1,
            "metric": "CLOSING_COUNT",
            "reason": "Jumlah Closing selesai dalam periode target",
        },
    )
    assert binding.status_code == 201, binding.text
    calculation: dict[str, Any] = {"target_version": 1, "request_id": "closing-business-proof"}
    calculated = await ctx.client.post(
        f"/api/v1/business/targets/{target_id}/calculate",
        headers=headers["lead"],
        json=calculation,
    )
    assert calculated.status_code == 201, calculated.text
    observation = calculated.json()
    assert observation["value"] == "1"
    assert observation["verification_state"] == "PENDING_VERIFICATION"
    assert observation["source_mode"] == "SOURCE_LINKED" and observation["evidence_refs"]
    replay = await ctx.client.post(
        f"/api/v1/business/targets/{target_id}/calculate",
        headers=headers["lead"],
        json=calculation,
    )
    assert replay.json()["observation_id"] == observation["observation_id"]
    for forbidden in (ctx.unrelated_headers, ctx.cross_org_headers, ctx.cross_tenant_headers):
        denied = await ctx.client.post(
            f"/api/v1/business/targets/{target_id}/calculate",
            headers=forbidden,
            json=calculation,
        )
        assert denied.status_code in (403, 404), denied.text
    forged = {
        key: value
        for key, value in observation.items()
        if key
        in {
            "observation_id",
            "target_id",
            "target_version",
            "kind",
            "value",
            "unit",
            "period",
            "source_ref",
            "source_mode",
            "observed_at",
            "verification_state",
            "evidence_refs",
        }
    }
    forged.update(observation_id="observation.forged.closing", value=999)
    attempt = await ctx.client.post(
        f"/api/v1/strategy/targets/{target_id}/observations",
        headers=headers["lead"],
        json=forged,
    )
    assert attempt.status_code == 409, attempt.text
    verified = await ctx.client.post(
        f"/api/v1/strategy/targets/{target_id}/observations/{observation['observation_id']}/verification",
        headers=headers["lead"],
        json={
            "verification_state": "VERIFIED",
            "reason": "Closing dan bukti perhitungan diperiksa",
        },
    )
    assert verified.status_code == 201, verified.text
    assert verified.json()["supersedes_observation_id"] == observation["observation_id"]
    performance = await ctx.client.get(
        "/api/v1/business/executive/performance", headers=ctx.executive_headers
    )
    assert performance.status_code == 200, performance.text
    detail = next(
        item
        for item in performance.json()["target_details"]
        if item["target"]["target_id"] == target_id
    )
    assert detail["selected_observations"]["actual"]["verification_state"] == "VERIFIED"
    assert detail["selected_observations"]["actual"]["value"] == "1"
    sales = next(item for item in performance.json()["domains"] if item["domain"] == "sales")
    assert next(item["value"] for item in sales["metrics"] if item["code"] == "closings") == 1
    assert (
        next(item["value"] for item in sales["metrics"] if item["code"] == "recorded_sales_value")
        == "125.00"
    )

    analytics_query = "?from=2027-03-01&to=2027-03-31&granularity=MONTH"
    analytics_response = await ctx.client.get(
        f"/api/v1/business/sales/analytics{analytics_query}", headers=headers["lead"]
    )
    assert analytics_response.status_code == 200, analytics_response.text
    analytics = analytics_response.json()
    assert analytics["period"] == {
        "from": "2027-03-01",
        "to": "2027-03-31",
        "granularity": "MONTH",
    }
    series = {item["code"]: item for item in analytics["series"]}
    assert series["closing_count"]["points"] == [{"period": "2027-03-01", "value": 1}]
    assert series["closing_value"]["points"] == [{"period": "2027-03-01", "value": "125.00"}]
    funnel = next(item for item in analytics["breakdowns"] if item["code"] == "sales_funnel")
    assert {item["code"]: item["value"] for item in funnel["items"]} == {
        "Lead": 0,
        "Qualified": 0,
        "Survey": 0,
        "Booking": 0,
        "closing": 1,
    }
    empty_period = await ctx.client.get(
        "/api/v1/business/sales/analytics?from=2026-03-01&to=2026-03-31&granularity=MONTH",
        headers=headers["lead"],
    )
    assert empty_period.status_code == 200, empty_period.text
    empty_closing_count = next(
        item for item in empty_period.json()["series"] if item["code"] == "closing_count"
    )
    assert empty_closing_count["available"] is True
    assert empty_closing_count["points"] == []

    executive_analytics_response = await ctx.client.get(
        f"/api/v1/business/executive/analytics{analytics_query}",
        headers=ctx.executive_headers,
    )
    assert executive_analytics_response.status_code == 200, executive_analytics_response.text
    executive_analytics = executive_analytics_response.json()
    assert executive_analytics["domain"] == "executive"
    company_closing = next(
        item for item in executive_analytics["series"] if item["code"] == "closing_count"
    )
    assert company_closing["points"] == [{"period": "2027-03-01", "value": 1}]
    assert any(
        item["code"].startswith("strategy_target_actual_")
        and any(value["actual_value"] == 1 for value in item["items"])
        for item in executive_analytics["comparisons"]
    )

    for forbidden in (ctx.unrelated_headers, ctx.cross_org_headers, ctx.cross_tenant_headers):
        denied = await ctx.client.get(
            f"/api/v1/business/sales/analytics{analytics_query}", headers=forbidden
        )
        assert denied.status_code in (403, 404), denied.text

    today = datetime.now(UTC).date()
    overdue_receivable = await business.create(
        "finance",
        "receivables",
        {
            "reference": "ANALYTICS-AR-OVERDUE",
            "due_date": (today - timedelta(days=1)).isoformat(),
            "amount": "100.00",
        },
    )
    not_due_receivable = await business.create(
        "finance",
        "receivables",
        {
            "reference": "ANALYTICS-AR-NOT-DUE",
            "due_date": (today + timedelta(days=4)).isoformat(),
            "amount": "75.00",
        },
    )
    payable_due = await business.create(
        "finance",
        "payables",
        {
            "reference": "ANALYTICS-AP-DUE",
            "due_date": (today - timedelta(days=1)).isoformat(),
            "amount": "50.00",
        },
    )
    await business.create(
        "finance",
        "payables",
        {
            "reference": "ANALYTICS-AP-NOT-DUE",
            "due_date": (today + timedelta(days=4)).isoformat(),
            "amount": "25.00",
        },
    )
    await business.create(
        "finance",
        "receivable_payments",
        {
            "receivable_id": overdue_receivable["receivable_id"],
            "payment_date": today.isoformat(),
            "amount": "20.00",
            "reference": "ANALYTICS-RECEIPT",
        },
    )
    finance_query = (
        f"?from={today.replace(day=1).isoformat()}&to={today.isoformat()}&granularity=MONTH"
    )
    finance_analytics_response = await ctx.client.get(
        f"/api/v1/business/finance/analytics{finance_query}", headers=headers["lead"]
    )
    assert finance_analytics_response.status_code == 200, finance_analytics_response.text
    finance_analytics = finance_analytics_response.json()
    exposure = next(
        item for item in finance_analytics["breakdowns"] if item["code"] == "payment_exposure"
    )
    exposure_values = {item["code"]: item["value"] for item in exposure["items"]}
    assert exposure_values == {
        "receivable_not_due": "75.00",
        "receivable_due": "80.00",
        "payable_not_due": "25.00",
        "payable_due": "50.00",
    }
    receipts = next(
        item for item in finance_analytics["series"] if item["code"] == "receipts_amount"
    )
    assert receipts["points"] == [{"period": today.replace(day=1).isoformat(), "value": "20.00"}]
    assert payable_due["payable_id"]
    assert not_due_receivable["receivable_id"]

    recruitment = await business.create(
        "hr",
        "recruitments",
        {
            "position_title": "Analytics Candidate Role",
            "opened_at": datetime.now(UTC).isoformat(),
        },
    )
    await business.create(
        "hr",
        "candidates",
        {
            "recruitment_id": recruitment["recruitment_id"],
            "full_name": "Analytics Candidate",
            "email": "analytics-candidate@e2e.local",
        },
    )
    hr_analytics_response = await ctx.client.get(
        f"/api/v1/business/hr/analytics{finance_query}", headers=headers["lead"]
    )
    assert hr_analytics_response.status_code == 200, hr_analytics_response.text
    hr_analytics = hr_analytics_response.json()
    candidate_funnel = next(
        item for item in hr_analytics["breakdowns"] if item["code"] == "candidate_funnel"
    )
    assert {item["code"]: item["value"] for item in candidate_funnel["items"]}["APPLIED"] == 1
    candidate_trend = next(
        item for item in hr_analytics["series"] if item["code"] == "candidate_count"
    )
    assert candidate_trend["points"] == [{"period": today.replace(day=1).isoformat(), "value": 1}]

    await business.create(
        "it",
        "incidents",
        {
            "title": "Analytics Incident",
            "description": "Incident analytics integration test",
            "severity": "HIGH",
        },
    )
    it_analytics_response = await ctx.client.get(
        f"/api/v1/business/it/analytics{finance_query}", headers=headers["lead"]
    )
    assert it_analytics_response.status_code == 200, it_analytics_response.text
    it_analytics = it_analytics_response.json()
    incident_statuses = next(
        item for item in it_analytics["breakdowns"] if item["code"] == "incidents_by_status"
    )
    assert {item["code"]: item["value"] for item in incident_statuses["items"]}["OPEN"] == 1
    incident_trend = next(
        item for item in it_analytics["series"] if item["code"] == "incident_count"
    )
    assert incident_trend["points"] == [{"period": today.replace(day=1).isoformat(), "value": 1}]
