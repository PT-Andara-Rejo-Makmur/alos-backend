"""Backend-owned research findings and lineage metadata."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    statement: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ResearchFindingLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(min_length=3)
    source_ref: str | None = None
    evidence_ref: str | None = None
    run_id: str | None = None
    domain: ResearchDomain = ResearchDomain.TECHNOLOGY
    freshness: str = "CURRENT"
    retention_expires_at: str | None = None
