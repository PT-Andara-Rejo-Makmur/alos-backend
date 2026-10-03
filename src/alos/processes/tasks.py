"""Connect human process work to the existing canonical Task service."""

from typing import Any

from sqlalchemy import select, update

from alos.domains.record_repository import conflict
from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.processes.authority import assigned, revalidate
from alos.processes.service import ProcessService
from alos.security.errors import PlatformError


async def create_task(
    processes: ProcessService,
    work: SharedWorkService,
    principal: Principal,
    process_id: str,
    step_id: str,
    reason: str,
) -> dict[str, Any]:
    if "work.write" not in principal.permissions:
        raise PlatformError(
            "PROCESS_TASK_DENIED", "Kewenangan penugasan diperlukan.", status_code=403
        )
    repository = processes.repository
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        record = await processes.load(session, principal, process_id)
        policies = await processes.table(session, "_policies")
        version = await session.scalar(
            select(policies.c.version)
            .where(
                policies.c.policy_id == record["policy_id"],
                policies.c.active.is_(True),
            )
            .with_for_update(read=True)
        )
        table = await processes.table(session, "es")
        record = dict(
            (
                await session.execute(
                    select(table).where(table.c.process_id == process_id).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        step = next(
            (
                item
                for item in await processes.steps(session, process_id)
                if item["step_id"] == step_id
            ),
            None,
        )
        if step is None or not assigned(step, principal):
            raise PlatformError(
                "PROCESS_TASK_DENIED", "Penugasan berada di luar kewenangan aktif.", status_code=403
            )
        if record["policy_version"] != version or record["status"] not in {"READY", "IN_PROGRESS"}:
            raise conflict("Alur atau aturan perusahaan telah berubah.")
        if step["task_id"] is not None:
            tasks = await repository.table(session, "core", "tasks")
            row = dict(
                (await session.execute(select(tasks).where(tasks.c.task_id == step["task_id"])))
                .mappings()
                .one()
            )
            return work._projection(row, principal)
        if step["kind"] != "EXECUTION" or step["status"] not in {"READY", "IN_PROGRESS"}:
            raise conflict("Penugasan membutuhkan langkah pelaksanaan yang siap dikerjakan.")
        task = await work.create_task_in_session(
            session,
            principal,
            {
                "title": step["instruction"],
                "description": reason,
                "due_at": step["due_at"].isoformat() if step["due_at"] else None,
            },
        )
        steps = await processes.table(session, "_steps")
        await session.execute(
            update(steps).where(steps.c.step_id == step_id).values(task_id=task["task_id"])
        )
        await processes.history(session, record, principal, "TASK_LINKED", reason, [], step_id)
        return task
