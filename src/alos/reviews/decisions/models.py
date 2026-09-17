from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DecisionOutcome(StrEnum):
    APPROVE = "APPROVE"
    RETURN = "RETURN"
    REJECT = "REJECT"
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
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    review_id: str
    authority: AuthorityLevel
    outcome: DecisionOutcome
    actor_id: str
    rationale: str
    decided_at: datetime
