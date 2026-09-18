"""Bounded worker boundary with explicit handlers and safe failure codes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from alos.jobs import InMemoryJobRepository, Job

JobHandler = Callable[[Job], Awaitable[dict[str, Any]]]


class JobWorker:
    def __init__(
        self,
        repository: InMemoryJobRepository,
        handlers: Mapping[str, JobHandler],
        *,
        worker_id: str,
    ) -> None:
        self._repository = repository
        self._handlers = dict(handlers)
        self._worker_id = worker_id

    async def run_once(self) -> Job | None:
        job = await self._repository.claim(self._worker_id)
        if job is None:
            return None
        handler = self._handlers.get(job.job_type.value)
        if handler is None:
            return await self._repository.fail(job.job_id, "HANDLER_NOT_CONFIGURED")
        try:
            result = await handler(job)
        except Exception:  # Handler internals must never leak into persisted error state.
            return await self._repository.fail(job.job_id, "SAFE_HANDLER_FAILURE")
        return await self._repository.succeed(job.job_id, result)


__all__ = ["JobHandler", "JobWorker"]
