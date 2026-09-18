"""Scheduler boundary that enqueues confirmed jobs without an external queue."""

from collections.abc import Iterable

from alos.jobs import EnqueueJob, InMemoryJobRepository, Job


class JobScheduler:
    def __init__(self, repository: InMemoryJobRepository) -> None:
        self._repository = repository

    async def enqueue(self, commands: Iterable[EnqueueJob]) -> tuple[Job, ...]:
        return tuple([await self._repository.enqueue(command) for command in commands])


__all__ = ["JobScheduler"]
