"""Backend-owned research findings and lineage metadata."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FreshnessStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class FindingKind(StrEnum):
    RESEARCH = "RESEARCH"
    OPERATIONAL = "OPERATIONAL"


class ResearchDomain(StrEnum):
    TECHNOLOGY = "TECHNOLOGY"
    PROPERTY_BUSINESS = "PROPERTY_BUSINESS"
    MANAGEMENT = "MANAGEMENT"
    PROPERTY_MARKET = "PROPERTY_MARKET"


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(min_length=3)
    kind: FindingKind = FindingKind.RESEARCH
    domain: ResearchDomain = ResearchDomain.TECHNOLOGY
    title: str | None = None
    summary: str | None = None
    statement: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    source_ref: str | None = None
    retrieval_metadata: dict[str, object] = Field(default_factory=dict)
    impact: str | None = None
    priority: str | None = None
    owner: str | None = None
    assumption: str | None = None
    conflict: str | None = None
    status: str = "ACTIVE"
    actor_id: str | None = None
    workspace_id: str | None = None
    scope_ref: str | None = None
    classification: str = "INTERNAL"
    created_at: str | None = None
    updated_at: str | None = None
    correlation_id: str | None = None


class ResearchRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation_id: str = Field(min_length=3)
    finding_id: str = Field(min_length=3)
    recommendation: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    priority_suggestion: str = Field(min_length=1)
    owner_suggestion: str | None = None
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    domain: ResearchDomain = ResearchDomain.TECHNOLOGY
    correlation_id: str | None = None


class BacklogCandidateState(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class BacklogCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=3)
    finding_id: str = Field(min_length=3)
    recommendation_id: str = Field(min_length=3)
    title: str | None = None
    summary: str | None = None
    impact: str = Field(min_length=1)
    priority_suggestion: str = Field(min_length=1)
    owner_suggestion: str | None = None
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    approval_state: BacklogCandidateState = BacklogCandidateState.DRAFT
    actor_id: str = Field(min_length=1)
    scope_ref: str | None = None
    correlation_id: str | None = None
    reviewed_by: str | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    reason: str | None = None


class ResearchFindingLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(min_length=3)
    source_ref: str | None = None
    evidence_ref: str | None = None
    run_id: str | None = None
    domain: ResearchDomain = ResearchDomain.TECHNOLOGY
    freshness: FreshnessStatus = FreshnessStatus.CURRENT
    retention_expires_at: str | None = None
