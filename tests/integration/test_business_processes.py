"""Real PostgreSQL cross-workspace routing, stale state and explicit human actions."""

from typing import Any
from uuid import uuid4

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def configure(ctx: Context, kind: str, rules: dict[str, Any] | None = None) -> None:
    response = await ctx.client.post(
        "/api/v1/processes/policies",
        headers=ctx.headers["it"],
        json={
            "business_type": kind,
            "owning_workspace_id": "workspace_business",
            "routes": {
                "property": "workspace_business",
                "sales": "workspace_business",
                "finance": "workspace_else",
                "legal": "workspace_else",
                "hr": "workspace_business",
                "it": "workspace_business",
                "owner": "workspace_else",
                "executive": "workspace_exec",
            },
            "rules": rules or {},
            "reason": "Aturan perusahaan untuk pengujian",
        },
    )
    assert response.status_code == 200, response.text


async def submit(ctx: Context, kind: str, subject_id: str) -> dict[str, Any]:
    response = await ctx.client.post(
        "/api/v1/processes",
        headers=ctx.headers["member"],
        json={
            "business_type": kind,
            "subject_id": subject_id,
            "reason": "Pengajuan bisnis dengan bukti",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def action(
    ctx: Context,
    process: dict[str, Any],
    user: str,
    position: int,
    expected: int = 200,
    command: str = "COMPLETE",
) -> dict[str, Any]:
    response = await ctx.client.post(
        f"/api/v1/processes/{process['process_id']}/steps/{process['steps'][position]['step_id']}/actions",
        headers=ctx.headers[user],
        json={
            "action": command,
            "subject_snapshot": process["subject_snapshot"],
            "reason": "Pemeriksaan eksplisit dalam context divisi",
        },
    )
    assert response.status_code == expected, response.text
    return response.json()


async def change_order(ctx: Context) -> dict[str, Any]:
    project = await ctx.client.post(
        "/api/v1/projects",
        headers=ctx.headers["lead"],
        json={
            "code": uuid4().hex,
            "name": "Pekerjaan perusahaan",
        },
    )
    assert project.status_code == 201, project.text
    row = await ctx.create(
        "property",
        "change-orders",
        {
            "project_id": project.json()["project_id"],
            "change_number": uuid4().hex,
            "description": "Perubahan biaya terukur",
            "amount_delta": "25.00",
        },
    )
    return await ctx.transition("property", "change-orders", row["change_order_id"], "SUBMITTED")


async def test_cross_workspace_packet_and_ordered_authority(context: Context) -> None:
    await configure(
        context,
        "CHANGE_ORDER",
        {"independent_steps": ["FINANCE_REVIEW"], "due_hours": {"FINANCE_REVIEW": 24}},
    )
    record = await change_order(context)
    process = await submit(context, "CHANGE_ORDER", record["change_order_id"])
    assert [step["code"] for step in process["steps"]] == ["TECHNICAL_REVIEW", "FINANCE_REVIEW"]
    assert process["packet"]["amount_delta"] == "25.00"
    assert process["steps"][1]["due_at"] is None
    await action(context, process, "workspace", 1, 409)
    await action(context, process, "member", 0, 403)
    for user in ("org", "tenant", "none"):
        denied = await context.client.get(
            f"/api/v1/processes/{process['process_id']}", headers=context.headers[user]
        )
        assert denied.status_code == 404, denied.text
    process = await action(context, process, "lead", 0)
    assert process["steps"][1]["due_at"] is not None
    queue = await context.client.get(
        "/api/v1/processes/work-queue", headers=context.headers["workspace"]
    )
    assert queue.status_code == 200, queue.text
    assert any(item["process_id"] == process["process_id"] for item in queue.json()["items"])
    hidden = await context.client.get(
        f"/api/v1/property/change-orders/{record['change_order_id']}",
        headers=context.headers["workspace"],
    )
    assert hidden.status_code == 404
    process = await action(context, process, "workspace", 1)
    assert process["status"] == "COMPLETED"
    assert [event["workspace_id"] for event in process["history"]] == [
        "workspace_business",
        "workspace_business",
        "workspace_else",
    ]
    await action(context, process, "workspace", 1, 409)


async def test_policy_revision_requires_repeated_reviews_preserves_history(
    context: Context,
) -> None:
    record = await change_order(context)
    process = await submit(context, "CHANGE_ORDER", record["change_order_id"])
    process = await action(context, process, "lead", 0)
    await configure(context, "CHANGE_ORDER", {"executive_amount_limit": "10.00"})
    await action(context, process, "workspace", 1, 409)
    response = await context.client.post(
        f"/api/v1/processes/{process['process_id']}/resubmit",
        headers=context.headers["member"],
        json={"reason": "Aturan kewenangan berubah"},
    )
    assert response.status_code == 200, response.text
    revised = response.json()
    assert revised["revision"] == 2
    assert revised["steps"][0]["status"] == "READY"
    assert revised["steps"][-1]["kind"] == "DECISION"
    assert len(revised["history"]) == 3
    await action(context, process, "lead", 0, 403)


async def test_returned_submission_can_be_corrected_without_erasing_history(
    context: Context,
) -> None:
    await configure(context, "CHANGE_ORDER")
    record = await change_order(context)
    process = await submit(context, "CHANGE_ORDER", record["change_order_id"])
    await context.transition("property", "change-orders", record["change_order_id"], "DRAFT", 409)
    process = await action(context, process, "lead", 0)
    process = await action(context, process, "workspace", 1, command="RETURN")
    queue = await context.client.get(
        "/api/v1/processes/work-queue", headers=context.headers["member"]
    )
    returned = next(
        item for item in queue.json()["items"] if item["process_id"] == process["process_id"]
    )
    assert returned["can_resubmit"]
    await context.transition("property", "change-orders", record["change_order_id"], "DRAFT")
    patched = await context.client.patch(
        f"/api/v1/property/change-orders/{record['change_order_id']}",
        headers=context.headers["member"],
        json={"description": "Perubahan setelah pemeriksaan Finance"},
    )
    assert patched.status_code == 200, patched.text
    await context.transition("property", "change-orders", record["change_order_id"], "SUBMITTED")
    revised = await context.client.post(
        f"/api/v1/processes/{process['process_id']}/resubmit",
        headers=context.headers["member"],
        json={"reason": "Dokumen diperbaiki sesuai pemeriksaan"},
    )
    assert revised.status_code == 200, revised.text
    new = revised.json()
    assert new["revision"] == 2 and new["subject_snapshot"] != process["subject_snapshot"]
    assert len(new["history"]) == 4
    await action(context, process, "workspace", 1, 409)
    new = await action(context, new, "lead", 0)
    new = await action(context, new, "workspace", 1)
    assert new["status"] == "COMPLETED"
