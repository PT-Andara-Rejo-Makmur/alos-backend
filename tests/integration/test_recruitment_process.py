"""Workforce decisions follow the requesting division and current company rules."""

from datetime import UTC, datetime

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, configure, submit

context = migrated_context
pytestmark = pytest.mark.asyncio(loop_scope="module")


async def test_workforce_reviews_are_scoped_current_and_policy_bounded(context: Context) -> None:
    ctx = context
    await configure(
        ctx, "RECRUITMENT", {"finance_review_required": True, "executive_required": True}
    )
    need = await ctx.create(
        "hr",
        "recruitments",
        {
            "position_title": "Teknisi pemeliharaan",
            "department_code": "OPS",
            "employment_type": "PERMANENT",
            "opened_at": datetime.now(UTC).isoformat(),
            "requesting_workspace_id": "workspace_business",
            "headcount": 2,
            "reason": "Kebutuhan pemeliharaan sesuai rencana divisi",
        },
    )
    process = await submit(ctx, "RECRUITMENT", need["recruitment_id"])
    assert [step["code"] for step in process["steps"]] == [
        "DIVISION_NEED_REVIEW",
        "HR_RECRUITMENT_REVIEW",
        "FINANCE_REVIEW",
        "EXECUTIVE_DECISION",
    ]
    # The persisted requesting division overrides the generic owner route.
    assert process["steps"][0]["workspace_id"] == "workspace_business"
    await action(ctx, process, "workspace", 0, 403)
    process = await action(ctx, process, "lead", 0)
    for user in ("org", "tenant"):
        hidden = await ctx.client.get(
            f"/api/v1/processes/{process['process_id']}", headers=ctx.headers[user]
        )
        assert hidden.status_code == 404
    changed = await ctx.client.patch(
        f"/api/v1/hr/recruitments/{need['recruitment_id']}",
        headers=ctx.headers["lead"],
        json={"headcount": 3},
    )
    assert changed.status_code == 200, changed.text
    await action(ctx, process, "lead", 1, 409)
    revised = await ctx.client.post(
        f"/api/v1/processes/{process['process_id']}/resubmit",
        headers=ctx.headers["member"],
        json={"reason": "Jumlah kebutuhan diperbaiki"},
    )
    assert revised.status_code == 200, revised.text
    process = revised.json()
    assert process["packet"]["headcount"] == 3 and process["revision"] == 2
    assert process["steps"][0]["workspace_id"] == "workspace_business"
    process = await action(ctx, process, "lead", 0)
    process = await action(ctx, process, "lead", 1)
    hidden = await ctx.client.get(
        f"/api/v1/hr/recruitments/{need['recruitment_id']}", headers=ctx.headers["workspace"]
    )
    assert hidden.status_code == 404
    process = await action(ctx, process, "workspace", 2)
    process = await action(ctx, process, "executive", 3)
    assert process["status"] == "COMPLETED"
    assert {event["workspace_id"] for event in process["history"]} == {
        "workspace_business",
        "workspace_else",
        "workspace_exec",
    }
