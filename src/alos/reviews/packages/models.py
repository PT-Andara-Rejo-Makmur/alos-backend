from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReviewPackageReference(BaseModel):
    """Backend-owned authoritative reference; package shape remains in alos-contracts."""

    model_config = ConfigDict(extra="forbid")
    review_id: str
    tenant_id: str
    workspace_id: str
    subject_id: str
    subject_version: str
    contract_version: str
    evidence_uri: str
    recorded_at: datetime
