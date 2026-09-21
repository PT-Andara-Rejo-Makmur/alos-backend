"""Typed contracts used by the Backend skill runtime and API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillDefinitionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(min_length=1, max_length=200)
    skill_version: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=250)
    description: str = Field(min_length=1, max_length=2000)
    purpose: str | None = Field(default=None, max_length=2000)
    when_to_use: list[str] = Field(default_factory=list)
    input_schema_ref: str = Field(min_length=1, max_length=1000)
    output_schema_ref: str = Field(min_length=1, max_length=1000)
    procedure: list[str] = Field(default_factory=list)
    required_tool_ids: list[str] = Field(default_factory=list)
    permission_refs: list[str] = Field(default_factory=list)
    scope_refs: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    escalation: list[str] = Field(default_factory=list)
    evaluation: list[str] = Field(default_factory=list)
    owner_actor_id: str | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None


class SkillVersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    skill_version: str


class SkillAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=200)
    skill_id: str = Field(min_length=1, max_length=200)
    skill_version: str = Field(min_length=1, max_length=50)


class SkillAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    skill_id: str
    skill_version: str
    status: Literal["ASSIGNED", "REJECTED"] = "ASSIGNED"
    assigned_by: str
    correlation_id: str
    scope_refs: list[str] = Field(default_factory=list)
    permission_refs: list[str] = Field(default_factory=list)


class SkillListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skills: list[dict[str, object]] = Field(default_factory=list)


class SkillDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    skill_version: str
    name: str
    description: str
    purpose: str | None = None
    status: str
    owner: str | None = None
    scope_refs: list[str] = Field(default_factory=list)
    permission_refs: list[str] = Field(default_factory=list)
    required_tool_ids: list[str] = Field(default_factory=list)


class SkillError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    correlation_id: str | None = None
