from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ReleaseAction(StrEnum):
    ACTIVATE = "ACTIVATE"
    HOLD = "HOLD"
    ROLLBACK = "ROLLBACK"


class ReleaseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: str
    review_id: str
    action: ReleaseAction
    actor_id: str
    decided_at: datetime
    rationale: str
