"""Typed contracts used by the Backend skill runtime and API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillVersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    skill_version: str


class SkillAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=200)
    agent_version: str = Field(min_length=1, max_length=50)
    skill_id: str = Field(min_length=1, max_length=200)
    skill_version: str = Field(min_length=1, max_length=50)
    proposed_agent_version: str | None = Field(default=None, min_length=1, max_length=50)


class SkillAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    base_agent_version: str
    draft_agent_version: str
    skill_ref: SkillVersionRef
    lifecycle_state: Literal["DRAFT"] = "DRAFT"
    correlation_id: str
