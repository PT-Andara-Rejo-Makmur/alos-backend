"""Backend-owned append-only audit domain records."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    entity_type: str
    entity_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    actor_id: str
    correlation_id: str
    outcome: str
    occurred_at: datetime
    actor_kind: Literal["HUMAN", "SYSTEM"] = "HUMAN"
    reason: str = "Authority state changed"
    metadata: dict[str, Any] = field(default_factory=dict)
