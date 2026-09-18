from datetime import UTC, datetime, timedelta

import pytest

from alos.jobs import EnqueueJob, InMemoryJobRepository, JobStatus, JobType
from alos.jobs.recovery import recover_stale_jobs
from alos.jobs.worker import JobWorker


def command(*, max_attempts: int = 3) -> EnqueueJob:
    return EnqueueJob(
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        job_type=JobType.GENESIS_REQUEST,
        payload={"request_id": "request_001"},
        correlation_id="corr_job_001",
        idempotency_key="job-request-001",
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_job_enqueue_is_idempotent_and_claim_is_exclusive() -> None:
    repository = InMemoryJobRepository()
    first = await repository.enqueue(command())
    duplicate = await repository.enqueue(command())
    claimed = await repository.claim("worker-1")
    second_claim = await repository.claim("worker-2")

    assert duplicate.job_id == first.job_id
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    assert second_claim is None


@pytest.mark.asyncio
async def test_worker_persists_safe_failure_without_leaking_exception() -> None:
    repository = InMemoryJobRepository()
    queued = await repository.enqueue(command(max_attempts=1))

    async def failing_handler(_job: object) -> dict[str, object]:
        raise RuntimeError("secret provider detail")

    worker = JobWorker(
        repository,
        {JobType.GENESIS_REQUEST.value: failing_handler},  # type: ignore[dict-item]
        worker_id="worker-1",
    )
    failed = await worker.run_once()

    assert failed is not None
    assert failed.job_id == queued.job_id
    assert failed.status == JobStatus.FAILED
    assert failed.safe_error_code == "SAFE_HANDLER_FAILURE"
    assert "secret" not in failed.safe_error_code.lower()


@pytest.mark.asyncio
async def test_stale_job_recovery_requeues_with_bounded_attempts() -> None:
    repository = InMemoryJobRepository()
    await repository.enqueue(command(max_attempts=2))
    claimed_at = datetime.now(UTC)
    claimed = await repository.claim("worker-1", now=claimed_at)
    assert claimed is not None

    recovered = await recover_stale_jobs(
        repository,
        lease_timeout=timedelta(minutes=5),
        now=claimed_at + timedelta(minutes=10),
    )

    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.QUEUED
    assert recovered[0].safe_error_code == "STALE_LEASE_RECOVERED"
