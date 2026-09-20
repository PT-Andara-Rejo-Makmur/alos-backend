"""Backend-owned API response models, not cross-repository contract copies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from alos.research import ResearchDomain, ResearchSourceMode


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"] = "ready"
    database_configured: bool
    genesis_configured: bool


class SystemInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    version: str
    environment: str
    authority: Literal["ALOS_BACKEND"] = "ALOS_BACKEND"


class ResearchRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=10, max_length=10_000)
    source_mode: ResearchSourceMode
    domain: ResearchDomain
