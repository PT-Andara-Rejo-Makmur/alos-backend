"""Backend-owned source metadata, immutable versions, and access context."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VERIFIED = "VERIFIED"
    RETIRED = "RETIRED"


class KnowledgeAccessContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=3, max_length=128)
    organization_id: str = Field(min_length=3, max_length=128)
    workspace_id: str = Field(min_length=3, max_length=128)
    actor_id: str = Field(min_length=3, max_length=128)
    correlation_id: str = Field(min_length=3, max_length=128)
    scope_refs: frozenset[str] = Field(min_length=1)
    data_classification: Literal[
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"
    ] = "INTERNAL"


class SourceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=3, max_length=128)
    source_version: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    source_type: Literal["DOCX", "PDF", "TEXT", "URL"] = "TEXT"
    data_classification: Literal[
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"
    ] = "INTERNAL"
    storage_uri: str = Field(min_length=3, max_length=2_000)
    content: str = Field(min_length=1, max_length=200_000)
    document_id: str | None = Field(default=None, min_length=3, max_length=128)


class SourceVersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str
    organization_id: str
    workspace_id: str
    source_id: str
    source_version: str
    title: str
    source_type: str
    data_classification: str
    storage_uri: str
    content_hash: str
    content: str
    document_id: str | None
    status: SourceStatus
    created_by: str
    created_at: datetime
    verified_by: str | None = None
    verified_at: datetime | None = None
