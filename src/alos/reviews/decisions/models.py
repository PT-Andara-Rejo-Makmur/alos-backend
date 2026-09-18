from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DecisionOutcome(StrEnum):
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    HOLD = "HOLD"


class AuthorityLevel(StrEnum):
    IT = "IT"
    DIRECTOR = "DIRECTOR"


class AIRecommendationReference(BaseModel):
    """Advisory reference; deliberately has no approval outcome field."""

    model_config = ConfigDict(extra="forbid")
    recommendation_id: str
    review_id: str
    summary: str
    recorded_at: datetime


class AuthoritativeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    decision_id: str
    review_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None
    release_id: str | None = None
    correlation_id: str | None = None
    subject_id: str | None = None
    authority: AuthorityLevel = Field(alias="decision_type")
    outcome: DecisionOutcome
    actor_id: str = Field(alias="decided_by")
    rationale: str
    decided_at: datetime
