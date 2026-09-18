"""Canonical Backend document metadata and immutable version references."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DataClassification = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_id: str = Field(min_length=3, max_length=128)
    tenant_id: str = Field(min_length=3, max_length=128)
    organization_id: str = Field(min_length=3, max_length=128)
    workspace_id: str = Field(min_length=3, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=128)
    data_classification: DataClassification
    status: Literal["DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "RETIRED"] = "DRAFT"
    owner_actor_id: str = Field(min_length=3, max_length=128)
    created_at: datetime


class DocumentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_id: str
    version: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=3, max_length=128)
    source_version: str = Field(min_length=1, max_length=100)
    storage_uri: str = Field(min_length=3, max_length=2_000)
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    created_by: str
    created_at: datetime
