"""Sales commands produce evidenced actuals, verification and Executive performance."""

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
