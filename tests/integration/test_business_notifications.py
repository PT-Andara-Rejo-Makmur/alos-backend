"""The SQL scheduler delivers configured reminders once to current membership audiences."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import change_order, configure, submit

from alos.jobs.models import JobType
from alos.jobs.sql_repository import SqlJobRepository
from alos.jobs.worker import JobWorker
from alos.notifications.business import BusinessNotifications
from alos.persistence.models import JobQueueRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def test_reminders_and_escalation_are_configured_scoped_and_idempotent(
    context: Context,
) -> None:
    ctx = context
    await configure(
        ctx,
        "CHANGE_ORDER",
        {
            "due_hours": {"TECHNICAL_REVIEW": 2},
            "reminder_hours": {"TECHNICAL_REVIEW": 1},
            "escalation_hours": {"TECHNICAL_REVIEW": 1},
            "escalation_routes": {
                "TECHNICAL_REVIEW": {"workspace_id": "workspace_else", "role": "DIVISION_LEAD"}
            },
        },
    )
    record = await change_order(ctx)
    process = await submit(ctx, "CHANGE_ORDER", record["change_order_id"])
    due = datetime.fromisoformat(process["due_at"])
    notices = BusinessNotifications(ctx.app.state.process_service.repository)
    await notices.schedule_due(now=due - timedelta(minutes=30))
    await notices.schedule_due(now=due + timedelta(hours=2))

    async def count() -> int:
        async with ctx.app.state.database.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(JobQueueRecord)
                    .where(JobQueueRecord.job_type == "NOTIFICATION")
                )
                or 0
            )

    before = await count()
    await notices.schedule_due(now=due + timedelta(hours=2))
    assert await count() == before
    worker = JobWorker(
        SqlJobRepository(ctx.app.state.database.session_factory),
        {JobType.NOTIFICATION.value: notices.deliver},
        worker_id="business-notification-test",
    )
    completed = []
    for _ in range(10):
        job = await worker.run_once()
        if job is None:
            break
        completed.append(job)
    assert completed and all(job.status == "SUCCEEDED" for job in completed)
    for user in ("org", "tenant", "none", "member"):
        response = await ctx.client.get("/api/v1/business/notifications", headers=ctx.headers[user])
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
    own = await ctx.client.get("/api/v1/business/notifications", headers=ctx.headers["lead"])
    assert {item["event"] for item in own.json()["items"]} == {
        "REVIEW_REQUESTED",
        "APPROACHING_DUE",
        "OVERDUE",
    }
    escalated = await ctx.client.get(
        "/api/v1/business/notifications", headers=ctx.headers["workspace"]
    )
    assert [item["event"] for item in escalated.json()["items"]] == ["ESCALATED"]
    assert "workspace_exec" not in [
        item["workspace_id"] for job in completed for item in [job.payload]
    ]
    assert datetime.now(UTC) < due
    task = await ctx.client.post(
        "/api/v1/tasks", headers=ctx.headers["lead"], json={"title": "Periksa pekerjaan manusia"}
    )
    assert task.status_code == 201, task.text
    finding = await ctx.client.post(
        "/api/v1/work/findings", headers=ctx.headers["lead"], json={"title": "Temuan operasional"}
    )
    assert finding.status_code == 201, finding.text
    queue = await ctx.client.get("/api/v1/business/work-queue", headers=ctx.headers["lead"])
    assert queue.status_code == 200, queue.text
    assert [item["task_id"] for item in queue.json()["tasks"]] == [task.json()["task_id"]]
    assert [item["finding_id"] for item in queue.json()["findings"]] == [
        finding.json()["finding_id"]
    ]
    assert [item["process_id"] for item in queue.json()["processes"]] == [process["process_id"]]
    foreign = await ctx.client.get("/api/v1/business/work-queue", headers=ctx.headers["tenant"])
    assert foreign.status_code == 200, foreign.text
    assert all(not items for items in foreign.json().values())
