"""HR contract supporting review and explicit Director requests remain scoped."""

from datetime import date
from uuid import uuid4

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, configure, submit
from test_material_approvals import decide, request

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def test_owner_direction_and_acknowledgement(context: Context) -> None:
    ctx = context
    await configure(ctx, "CHANGE_ORDER")
    project = await ctx.client.post(
        "/api/v1/projects",
        headers=ctx.headers["lead"],
        json={"code": uuid4().hex, "name": "Pekerjaan rutin"},
    )
    assert project.status_code == 201, project.text
    change = await ctx.create(
        "property",
        "change-orders",
        {
            "project_id": project.json()["project_id"],
            "change_number": uuid4().hex,
            "description": "Perubahan tanpa dampak biaya",
            "amount_delta": "0.00",
        },
        user="member",
    )
    await ctx.transition("property", "change-orders", change["change_order_id"], "SUBMITTED")
    process = await submit(ctx, "CHANGE_ORDER", change["change_order_id"])
    records_path = f"/api/v1/business/projects/{project.json()['project_id']}/records"
    records = await ctx.client.get(records_path, headers=ctx.headers["lead"])
    assert records.status_code == 200, records.text
    assert any(item["record_id"] == change["change_order_id"] for item in records.json()["items"])
    for user in ("org", "tenant", "workspace"):
        assert (await ctx.client.get(records_path, headers=ctx.headers[user])).status_code == 404
    assert [item["code"] for item in process["steps"]] == ["TECHNICAL_REVIEW"]
    path = f"/api/v1/processes/{process['process_id']}/request-direction"
    for user, expected in (("member", 403), ("org", 404), ("tenant", 404)):
        response = await ctx.client.post(
            path, headers=ctx.headers[user], json={"reason": "Minta arahan"}
        )
        assert response.status_code == expected, response.text
    assert (
        await ctx.client.post(path, headers=ctx.headers["lead"], json={"reason": " "})
    ).status_code == 422
    directed = await ctx.client.post(
        path, headers=ctx.headers["lead"], json={"reason": "Perlu arahan atas prioritas perusahaan"}
    )
    assert directed.status_code == 200, directed.text
    process = directed.json()
    assert process["direction_reason"] and process["steps"][-1]["status"] == "PENDING"
    assert (
        await ctx.client.post(path, headers=ctx.headers["lead"], json={"reason": "Ulang"})
    ).status_code == 409
    approval = await request(
        ctx, "PROPERTY_CHANGE_ORDER", change["change_order_id"], "APPROVE_CHANGE_ORDER"
    )
    process = await action(ctx, process, "lead", 0)
    await decide(ctx, approval, expected=409)
    process = await action(ctx, process, "executive", 1)
    await decide(ctx, approval)
    assert process["status"] == "COMPLETED"
    await configure(ctx, "CHANGE_ORDER", {"executive_acknowledgement": True})
    other = await ctx.create(
        "property",
        "change-orders",
        {
            "project_id": project.json()["project_id"],
            "change_number": uuid4().hex,
            "description": "Informasi pekerjaan rutin",
            "amount_delta": "0.00",
        },
    )
    await ctx.transition("property", "change-orders", other["change_order_id"], "SUBMITTED")
    acknowledgement = await submit(ctx, "CHANGE_ORDER", other["change_order_id"])
    acknowledgement = await action(ctx, acknowledgement, "lead", 0)
    await action(ctx, acknowledgement, "executive", 1, 409)
    acknowledgement = await action(ctx, acknowledgement, "executive", 1, command="ACKNOWLEDGE")
    assert acknowledgement["status"] == "COMPLETED"


async def test_hr_contract_owner_decision_follows_supporting_legal_review(context: Context) -> None:
    ctx = context
    employee = await ctx.create(
        "hr",
        "employees",
        {
            "employee_number": uuid4().hex,
            "full_name": "Staf perusahaan",
            "department_code": "OPS",
            "position_title": "Staf",
            "join_date": date.today().isoformat(),
        },
    )
    contract = await ctx.create(
        "hr",
        "employment-contracts",
        {
            "employee_id": employee["employee_id"],
            "contract_number": uuid4().hex,
            "contract_type": "PERMANENT",
            "start_date": date.today().isoformat(),
            "legal_review_required": True,
        },
    )
    identity = contract["employment_contract_id"]
    await ctx.transition("hr", "employment-contracts", identity, "IN_REVIEW")
    await ctx.transition("hr", "employment-contracts", identity, "APPROVED", 403, "member")
    await ctx.transition("hr", "employment-contracts", identity, "APPROVED", 409)
    await configure(ctx, "EMPLOYMENT_CONTRACT")
    process = await submit(ctx, "EMPLOYMENT_CONTRACT", identity)
    assert [item["code"] for item in process["steps"]] == ["HR_CONTRACT_REVIEW", "LEGAL_REVIEW"]
    process = await action(ctx, process, "lead", 0)
    await ctx.transition("hr", "employment-contracts", identity, "APPROVED", 409)
    hidden = await ctx.client.get(
        f"/api/v1/hr/employment-contracts/{identity}", headers=ctx.headers["workspace"]
    )
    assert hidden.status_code == 404
    process = await action(ctx, process, "workspace", 1)
    assert process["status"] == "COMPLETED"
    approved = await ctx.transition("hr", "employment-contracts", identity, "APPROVED")
    assert approved["status"] == "APPROVED"
    await ctx.transition("hr", "employment-contracts", identity, "ACTIVE", 409)
    assert (
        await ctx.client.patch(
            f"/api/v1/hr/employment-contracts/{identity}",
            headers=ctx.headers["lead"],
            json={"contract_number": "Rewrite"},
        )
    ).status_code == 409
