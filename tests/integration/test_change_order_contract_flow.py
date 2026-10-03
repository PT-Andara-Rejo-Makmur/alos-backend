"""Supporting Legal authority, explicit company decision and owner implementation."""

from uuid import uuid4

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, configure, submit
from test_material_approvals import decide, execute, request

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def test_change_order_reviews_contract_and_configured_director(context: Context) -> None:
    ctx = context
    await configure(ctx, "CHANGE_ORDER", {"executive_required": True})
    project = await ctx.client.post(
        "/api/v1/projects",
        headers=ctx.headers["lead"],
        json={
            "code": uuid4().hex,
            "name": "Perubahan proyek",
            "objective": "Perubahan kontrak terukur",
            "priority": "HIGH",
        },
    )
    assert project.status_code == 201, project.text
    change = await ctx.create(
        "property",
        "change-orders",
        {
            "project_id": project.json()["project_id"],
            "change_number": uuid4().hex,
            "description": "Perubahan pekerjaan dan kontrak",
            "amount_delta": "25.00",
            "schedule_impact_days": 3,
            "contract_change_required": True,
        },
        user="member",
    )
    await ctx.transition("property", "change-orders", change["change_order_id"], "SUBMITTED")
    process = await submit(ctx, "CHANGE_ORDER", change["change_order_id"])
    assert [item["code"] for item in process["steps"]] == [
        "TECHNICAL_REVIEW",
        "FINANCE_REVIEW",
        "LEGAL_REVIEW",
        "EXECUTIVE_DECISION",
    ]
    contract = await ctx.create(
        "legal",
        "contracts",
        {
            "contract_number": uuid4().hex,
            "contract_type": "WORK",
            "counterparty_name": "Kontraktor",
        },
        user="workspace",
    )
    await ctx.transition(
        "legal", "contracts", contract["contract_id"], "IN_REVIEW", user="workspace"
    )
    await ctx.transition(
        "legal", "contracts", contract["contract_id"], "REVIEWED", user="workspace"
    )
    origin_path = f"/api/v1/legal/contracts/{contract['contract_id']}/business-origin"
    origin = {
        "process_id": process["process_id"],
        "reason": "Kontrak diperiksa untuk perubahan pekerjaan",
    }
    for user in ("org", "tenant", "member"):
        denied = await ctx.client.post(origin_path, headers=ctx.headers[user], json=origin)
        assert denied.status_code in (403, 404), denied.text
    linked = await ctx.client.post(origin_path, headers=ctx.headers["workspace"], json=origin)
    assert linked.status_code == 200, linked.text
    await action(ctx, process, "lead", 0, 409)
    await ctx.transition("property", "change-orders", change["change_order_id"], "DRAFT")
    contract_reference = await ctx.client.patch(
        f"/api/v1/property/change-orders/{change['change_order_id']}",
        headers=ctx.headers["member"],
        json={"related_contract_id": contract["contract_id"]},
    )
    assert contract_reference.status_code == 200, contract_reference.text
    await ctx.transition("property", "change-orders", change["change_order_id"], "SUBMITTED")
    revised = await ctx.client.post(
        f"/api/v1/processes/{process['process_id']}/resubmit",
        headers=ctx.headers["member"],
        json={"reason": "Kontrak Legal telah disertakan dalam pengajuan"},
    )
    assert revised.status_code == 200, revised.text
    process = revised.json()
    assert process["packet"]["contracts"][0]["contract_id"] == contract["contract_id"]
    approval = await request(
        ctx, "PROPERTY_CHANGE_ORDER", change["change_order_id"], "APPROVE_CHANGE_ORDER"
    )
    await decide(ctx, approval, expected=409)
    process = await action(ctx, process, "lead", 0)
    process = await action(ctx, process, "workspace", 1)
    process = await action(ctx, process, "workspace", 2)
    await decide(ctx, approval, expected=409)
    process = await action(ctx, process, "executive", 3)
    assert process["status"] == "COMPLETED"
    await decide(ctx, approval)
    await execute(ctx, "property", "change-orders", change["change_order_id"], "APPROVED", approval)
    implemented = await ctx.client.post(
        f"/api/v1/property/change-orders/{change['change_order_id']}/implement",
        headers=ctx.headers["member"],
        json={"reason": "Pekerjaan perubahan telah dilaksanakan sesuai keputusan"},
    )
    assert implemented.status_code == 200, implemented.text
    assert implemented.json()["status"] == "IMPLEMENTED" and implemented.json()["implemented_at"]
    for record_type, identity, user in (
        ("PROPERTY_CHANGE_ORDER", change["change_order_id"], "member"),
        ("LEGAL_CONTRACT", contract["contract_id"], "workspace"),
    ):
        lineage = await ctx.client.get(
            f"/api/v1/business/relationships/{record_type}/{identity}", headers=ctx.headers[user]
        )
        assert lineage.status_code == 200 and len(lineage.json()["items"]) == 1, lineage.text
    hidden = await ctx.client.get(
        f"/api/v1/legal/contracts/{contract['contract_id']}", headers=ctx.headers["member"]
    )
    assert hidden.status_code == 404
