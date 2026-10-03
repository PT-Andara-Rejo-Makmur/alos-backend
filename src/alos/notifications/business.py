"""Transactional business notification intent, scheduled reminders and scoped in-app delivery."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository
from alos.identity import Principal
from alos.jobs.models import EnqueueJob, Job, JobType
from alos.jobs.sql_repository import SqlJobRepository
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import revalidate
from alos.processes.subjects import SUBJECTS

LABELS = {
    "ASSIGNED": "Pekerjaan ditugaskan",
    "REVIEW_REQUESTED": "Pemeriksaan diperlukan",
    "DECISION_REQUIRED": "Keputusan diperlukan",
    "ACKNOWLEDGEMENT": "Untuk diketahui",
    "RETURNED": "Pengajuan perlu diperbaiki",
    "DECISION_COMPLETED": "Keputusan tercatat",
    "PROCESS_COMPLETED": "Alur proses selesai",
    "APPROACHING_DUE": "Pekerjaan mendekati tenggat",
    "OVERDUE": "Pekerjaan melewati tenggat",
    "ESCALATED": "Arahan pimpinan diperlukan",
}


async def enqueue_notice(
    repository: RecordRepository,
    session: AsyncSession,
    process: dict[str, Any],
    step: dict[str, Any] | None,
    event: str,
    audience: dict[str, Any] | None = None,
) -> None:
    if audience is None:
        audience = (
            {
                "workspace_id": step["workspace_id"],
                "role_refs": [step["role"]],
                "permission": step["permission"],
                "recipient_actor_id": None,
            }
            if step
            else {
                "workspace_id": process["workspace_id"],
                "role_refs": ["DIVISION_LEAD", "DIVISION_MEMBER"],
                "permission": SUBJECTS[process["business_type"]][0] + ".read",
                "recipient_actor_id": process["requested_by"],
            }
        )
    step_id = step["step_id"] if step else None
    payload = {
        "process_id": process["process_id"],
        "revision": process["revision"],
        "step_id": step_id,
        "event": event,
        **audience,
    }
    await SqlJobRepository(repository.factory).enqueue_in_session(
        session,
        EnqueueJob(
            tenant_id=process["tenant_id"],
            organization_id=process["organization_id"],
            workspace_id=audience["workspace_id"],
            job_type=JobType.NOTIFICATION,
            payload=payload,
            correlation_id=current_correlation_id(),
            idempotency_key=(
                f"notice.{process['process_id']}.{process['revision']}.{step_id or 'owner'}.{event}"
            ),
        ),
    )


class BusinessNotifications:
    def __init__(self, repository: RecordRepository) -> None:
        self.repository = repository
        self.jobs = SqlJobRepository(repository.factory)

    async def deliver(self, job: Job) -> dict[str, Any]:
        values = job.payload
        event = values["event"]
        if event not in LABELS:
            raise ValueError("Unsupported business notification")
        async with self.repository.factory() as session, session.begin():
            # Fence the delivery itself, not just marking the worker result completed.
            await self.jobs._running(session, job.job_id, job.locked_by, job.attempts)
            processes = await self.repository.table(session, "core", "business_processes")
            process = (
                (
                    await session.execute(
                        select(processes).where(
                            processes.c.process_id == values["process_id"],
                            processes.c.tenant_id == job.tenant_id,
                            processes.c.organization_id == job.organization_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            if process["revision"] != values["revision"]:
                return {"delivered": False, "reason": "PROCESS_REVISED"}
            if values["step_id"] and event not in {"DECISION_COMPLETED", "PROCESS_COMPLETED"}:
                steps = await self.repository.table(session, "core", "business_process_steps")
                status = await session.scalar(
                    select(steps.c.status).where(
                        steps.c.step_id == values["step_id"],
                        steps.c.process_id == process["process_id"],
                        steps.c.revision == process["revision"],
                    )
                )
                if process["status"] not in {"READY", "IN_PROGRESS"} or status not in {
                    "READY",
                    "IN_PROGRESS",
                }:
                    return {"delivered": False, "reason": "WORK_ALREADY_CHANGED"}
            table = await self.repository.table(session, "core", "business_notifications")
            old = await session.scalar(
                select(table.c.notification_id).where(table.c.job_id == job.job_id)
            )
            if old:
                return {"delivered": True, "notification_id": old}
            identity = uuid4().hex
            await session.execute(
                insert(table).values(
                    notification_id=identity,
                    job_id=job.job_id,
                    tenant_id=job.tenant_id,
                    organization_id=job.organization_id,
                    workspace_id=job.workspace_id,
                    process_id=values["process_id"],
                    step_id=values["step_id"],
                    event=event,
                    title=LABELS[event],
                    role_refs=values["role_refs"],
                    permission=values["permission"],
                    recipient_actor_id=values["recipient_actor_id"],
                    created_at=datetime.now(UTC),
                )
            )
            return {"delivered": True, "notification_id": identity}

    async def list(self, principal: Principal) -> dict[str, Any]:
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            table = await self.repository.table(session, "core", "business_notifications")
            rows = (
                (
                    await session.execute(
                        select(table)
                        .where(
                            *self.repository.scope(table, principal),
                            table.c.permission.in_(principal.permissions),
                            or_(
                                table.c.recipient_actor_id.is_(None),
                                table.c.recipient_actor_id == principal.actor_id,
                            ),
                        )
                        .order_by(table.c.created_at.desc())
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "items": jsonable_encoder(
                    [
                        {
                            "notification_id": row["notification_id"],
                            "process_id": row["process_id"],
                            "event": row["event"],
                            "title": row["title"],
                            "workspace_id": row["workspace_id"],
                            "created_at": row["created_at"],
                        }
                        for row in rows
                        if set(row["role_refs"]) & principal.roles
                    ]
                )
            }

    async def schedule_due(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        enqueued = 0
        async with self.repository.factory() as session, session.begin():
            processes = await self.repository.table(session, "core", "business_processes")
            steps = await self.repository.table(session, "core", "business_process_steps")
            policies = await self.repository.table(session, "core", "business_process_policies")
            rows = (
                (
                    await session.execute(
                        select(
                            processes,
                            steps.c.step_id,
                            steps.c.code,
                            steps.c.workspace_id.label("assigned_workspace"),
                            steps.c.role,
                            steps.c.permission,
                            steps.c.due_at,
                            policies.c.rules,
                            policies.c.routes,
                        )
                        .join(
                            steps,
                            and_(
                                steps.c.process_id == processes.c.process_id,
                                steps.c.revision == processes.c.revision,
                            ),
                        )
                        .join(policies, policies.c.policy_id == processes.c.policy_id)
                        .where(
                            processes.c.status.in_(("READY", "IN_PROGRESS")),
                            steps.c.status.in_(("READY", "IN_PROGRESS")),
                            steps.c.due_at.is_not(None),
                            policies.c.active.is_(True),
                            policies.c.version == processes.c.policy_version,
                        )
                        .order_by(steps.c.due_at)
                        .limit(500)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                process = dict(row)
                step = {
                    "step_id": row["step_id"],
                    "workspace_id": row["assigned_workspace"],
                    "role": row["role"],
                    "permission": row["permission"],
                }
                hours = row["rules"].get("reminder_hours", {}).get(row["code"])
                if hours and row["due_at"] - timedelta(hours=hours) <= current < row["due_at"]:
                    await enqueue_notice(self.repository, session, process, step, "APPROACHING_DUE")
                    enqueued += 1
                if current >= row["due_at"]:
                    await enqueue_notice(self.repository, session, process, step, "OVERDUE")
                    enqueued += 1
                delay = row["rules"].get("escalation_hours", {}).get(row["code"])
                target = row["rules"].get("escalation_routes", {}).get(row["code"])
                if delay and target and current >= row["due_at"] + timedelta(hours=delay):
                    await enqueue_notice(
                        self.repository,
                        session,
                        process,
                        step,
                        "ESCALATED",
                        {
                            "workspace_id": target["workspace_id"],
                            "role_refs": [target["role"]],
                            "permission": "strategy.read"
                            if target["role"] == "EXECUTIVE"
                            else "work.read",
                            "recipient_actor_id": None,
                        },
                    )
                    enqueued += 1
        return enqueued
