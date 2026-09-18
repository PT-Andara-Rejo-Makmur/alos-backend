"""Deterministic job repository preserving MVP-1 idempotency and retry semantics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from alos.jobs.models import EnqueueJob, Job, JobStatus, JobType


class JobConflictError(ValueError):
    pass


class InMemoryJobRepository:
    """Test/local repository matching the authoritative SQL queue state machine."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._idempotency: dict[tuple[str, JobType, str], str] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, command: EnqueueJob) -> Job:
        key = (command.organization_id, command.job_type, command.idempotency_key)
        async with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                return self._jobs[existing_id]
            job = Job(
                job_id=str(uuid4()),
                tenant_id=command.tenant_id,
                organization_id=command.organization_id,
                workspace_id=command.workspace_id,
                job_type=command.job_type,
                payload=dict(command.payload),
                status=JobStatus.QUEUED,
                attempts=0,
                max_attempts=command.max_attempts,
                next_retry_at=command.next_retry_at,
                correlation_id=command.correlation_id,
                idempotency_key=command.idempotency_key,
                owner_actor_id=command.owner_actor_id,
                created_at=datetime.now(UTC),
            )
            self._jobs[job.job_id] = job
            self._idempotency[key] = job.job_id
            return job

    async def claim(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        job_types: frozenset[JobType] | None = None,
    ) -> Job | None:
        current_time = now or datetime.now(UTC)
        async with self._lock:
            eligible = sorted(self._jobs.values(), key=lambda item: item.created_at)
            for job in eligible:
                if job.status != JobStatus.QUEUED or job.next_retry_at > current_time:
                    continue
                if job.attempts >= job.max_attempts:
                    continue
                if job_types is not None and job.job_type not in job_types:
                    continue
                claimed = replace(
                    job,
                    status=JobStatus.RUNNING,
                    attempts=job.attempts + 1,
                    locked_by=worker_id,
                    locked_at=current_time,
                )
                self._jobs[job.job_id] = claimed
                return claimed
        return None

    async def succeed(self, job_id: str, result: dict[str, object]) -> Job:
        return await self._finish(job_id, JobStatus.SUCCEEDED, result=dict(result))

    async def fail(
        self,
        job_id: str,
        safe_error_code: str,
        *,
        retry_delay: timedelta = timedelta(seconds=30),
    ) -> Job:
        async with self._lock:
            job = self._require_running(job_id)
            terminal = job.attempts >= job.max_attempts
            updated = replace(
                job,
                status=JobStatus.FAILED if terminal else JobStatus.QUEUED,
                next_retry_at=datetime.now(UTC) + retry_delay,
                locked_by=None,
                locked_at=None,
                completed_at=datetime.now(UTC) if terminal else None,
                safe_error_code=safe_error_code,
            )
            self._jobs[job_id] = updated
            return updated

    async def recover_stale(
        self,
        *,
        stale_before: datetime,
        safe_error_code: str = "STALE_LEASE_RECOVERED",
    ) -> tuple[Job, ...]:
        recovered: list[Job] = []
        async with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                if (
                    job.status != JobStatus.RUNNING
                    or job.locked_at is None
                    or job.locked_at >= stale_before
                ):
                    continue
                terminal = job.attempts >= job.max_attempts
                updated = replace(
                    job,
                    status=JobStatus.FAILED if terminal else JobStatus.QUEUED,
                    locked_by=None,
                    locked_at=None,
                    completed_at=datetime.now(UTC) if terminal else None,
                    safe_error_code=safe_error_code,
                )
                self._jobs[job_id] = updated
                recovered.append(updated)
        return tuple(recovered)

    def get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise LookupError("job was not found") from exc

    async def _finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, object],
    ) -> Job:
        async with self._lock:
            job = self._require_running(job_id)
            updated = replace(
                job,
                status=status,
                result=result,
                completed_at=datetime.now(UTC),
                locked_by=None,
                locked_at=None,
            )
            self._jobs[job_id] = updated
            return updated

    def _require_running(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status != JobStatus.RUNNING:
            raise JobConflictError("job must be RUNNING")
        return job
