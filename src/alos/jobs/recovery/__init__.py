"""Stale-lease recovery boundary."""

from datetime import UTC, datetime, timedelta

from alos.jobs import InMemoryJobRepository, Job
from alos.jobs.sql_repository import SqlJobRepository


async def recover_stale_jobs(
    repository: InMemoryJobRepository | SqlJobRepository,
    *,
    lease_timeout: timedelta,
    now: datetime | None = None,
) -> tuple[Job, ...]:
    current = now or datetime.now(UTC)
    return await repository.recover_stale(stale_before=current - lease_timeout)


__all__ = ["recover_stale_jobs"]
