"""PostgreSQL queue using exclusive claims and fenced lease completion."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.jobs.models import EnqueueJob, Job, JobStatus, JobType
from alos.jobs.repository import JobConflictError
from alos.persistence.models import JobQueueRecord


def project(row: JobQueueRecord) -> Job:
    values = {key: getattr(row, key) for key in Job.__dataclass_fields__}
    values["status"], values["job_type"] = JobStatus(row.status), JobType(row.job_type)
    return Job(**values)


class SqlJobRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def enqueue(self, command: EnqueueJob) -> Job:
        async with self.factory.begin() as session:
            return await self.enqueue_in_session(session, command)

    async def enqueue_in_session(self, session: AsyncSession, command: EnqueueJob) -> Job:
        values = asdict(command)
        values["job_type"] = command.job_type.value
        await session.execute(
            insert(JobQueueRecord)
            .values(
                **values,
                job_id=uuid4().hex,
                status=JobStatus.QUEUED.value,
                attempts=0,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "job_type", "idempotency_key"]
            )
        )
        row = await session.scalar(
            select(JobQueueRecord).where(
                JobQueueRecord.organization_id == command.organization_id,
                JobQueueRecord.job_type == command.job_type.value,
                JobQueueRecord.idempotency_key == command.idempotency_key,
            )
        )
        assert row is not None
        if (row.tenant_id, row.workspace_id, row.payload, row.owner_actor_id) != (
            command.tenant_id,
            command.workspace_id,
            command.payload,
            command.owner_actor_id,
        ):
            raise JobConflictError("idempotency key has different scoped job inputs")
        return project(row)

    async def claim(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        job_types: frozenset[JobType] | None = None,
    ) -> Job | None:
        stamp = now or datetime.now(UTC)
        async with self.factory.begin() as session:
            query = select(JobQueueRecord).where(
                JobQueueRecord.status == JobStatus.QUEUED.value,
                JobQueueRecord.next_retry_at <= stamp,
                JobQueueRecord.attempts < JobQueueRecord.max_attempts,
            )
            if job_types is not None:
                query = query.where(JobQueueRecord.job_type.in_([kind.value for kind in job_types]))
            row = await session.scalar(
                query.order_by(JobQueueRecord.created_at, JobQueueRecord.job_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status, row.locked_by, row.locked_at = JobStatus.RUNNING.value, worker_id, stamp
            row.attempts += 1
            await session.flush()
            return project(row)

    async def succeed(
        self,
        job_id: str,
        result: dict[str, Any],
        *,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> Job:
        async with self.factory.begin() as session:
            row = await self._running(session, job_id, worker_id, attempt)
            row.status, row.result, row.completed_at = (
                JobStatus.SUCCEEDED.value,
                result,
                datetime.now(UTC),
            )
            row.locked_by, row.locked_at, row.safe_error_code = None, None, None
            await session.flush()
            return project(row)

    async def fail(
        self,
        job_id: str,
        safe_error_code: str,
        *,
        retry_delay: timedelta = timedelta(seconds=30),
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> Job:
        async with self.factory.begin() as session:
            row = await self._running(session, job_id, worker_id, attempt)
            terminal = row.attempts >= row.max_attempts
            row.status = JobStatus.FAILED.value if terminal else JobStatus.QUEUED.value
            row.next_retry_at = datetime.now(UTC) + retry_delay
            row.completed_at = datetime.now(UTC) if terminal else None
            row.safe_error_code, row.locked_by, row.locked_at = safe_error_code, None, None
            await session.flush()
            return project(row)

    async def recover_stale(
        self, *, stale_before: datetime, safe_error_code: str = "STALE_LEASE_RECOVERED"
    ) -> tuple[Job, ...]:
        async with self.factory.begin() as session:
            rows = (
                await session.scalars(
                    select(JobQueueRecord)
                    .where(
                        JobQueueRecord.status == JobStatus.RUNNING.value,
                        JobQueueRecord.locked_at < stale_before,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                terminal = row.attempts >= row.max_attempts
                row.status = JobStatus.FAILED.value if terminal else JobStatus.QUEUED.value
                row.completed_at = datetime.now(UTC) if terminal else None
                row.safe_error_code, row.locked_by, row.locked_at = safe_error_code, None, None
            await session.flush()
            return tuple(project(row) for row in rows)

    async def get(self, job_id: str) -> Job:
        async with self.factory() as session:
            row = await session.get(JobQueueRecord, job_id)
            if row is None:
                raise LookupError("job was not found")
            return project(row)

    @staticmethod
    async def _running(
        session: AsyncSession, job_id: str, worker_id: str | None, attempt: int | None
    ) -> JobQueueRecord:
        row = await session.scalar(
            select(JobQueueRecord)
            .where(
                JobQueueRecord.job_id == job_id,
            )
            .with_for_update()
        )
        if (
            row is None
            or row.status != JobStatus.RUNNING.value
            or worker_id is None
            or attempt is None
        ):
            raise JobConflictError("active worker lease is required")
        if row.locked_by != worker_id or row.attempts != attempt:
            raise JobConflictError("worker lease was superseded")
        return row
