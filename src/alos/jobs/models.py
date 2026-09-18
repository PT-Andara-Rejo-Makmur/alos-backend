"""Authoritative PostgreSQL-backed job lifecycle models."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobType(StrEnum):
    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    DOCUMENT_INDEXING = "DOCUMENT_INDEXING"
    GENESIS_REQUEST = "GENESIS_REQUEST"
    SCHEDULED_REPORT = "SCHEDULED_REPORT"
    RECURRING_MONITOR = "RECURRING_MONITOR"
    NOTIFICATION = "NOTIFICATION"
    CONNECTOR_SYNC = "CONNECTOR_SYNC"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class EnqueueJob:
    tenant_id: str
    organization_id: str
    workspace_id: str
    job_type: JobType
    payload: dict[str, Any]
    correlation_id: str
    idempotency_key: str
    owner_actor_id: str | None = None
    max_attempts: int = 3
    next_retry_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if len(self.idempotency_key) < 8:
            raise ValueError("idempotency_key must contain at least 8 characters")
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    job_type: JobType
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    next_retry_at: datetime
    correlation_id: str
    idempotency_key: str
    owner_actor_id: str | None
    created_at: datetime
    locked_by: str | None = None
    locked_at: datetime | None = None
    completed_at: datetime | None = None
    safe_error_code: str | None = None
    result: dict[str, Any] | None = None
