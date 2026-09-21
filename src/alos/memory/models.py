"""Typed backend-owned memory and evidence contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)

    memory_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=128)
    organization_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    division_id: str | None = Field(default=None, min_length=1, max_length=128)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    scope_refs: tuple[str, ...] = Field(default_factory=tuple)
    classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = "INTERNAL"
    content: str = Field(min_length=1, max_length=200_000)
    source_ref: str | None = Field(default=None, min_length=1, max_length=200)
    evidence_ref: str | None = Field(default=None, min_length=1, max_length=200)
    run_id: str | None = Field(default=None, min_length=1, max_length=200)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=200)
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)

    memory_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    run_id: str | None = None
    correlation_id: str | None = None
    classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = "INTERNAL"
    freshness: Literal["CURRENT", "STALE", "UNKNOWN"] = "CURRENT"
