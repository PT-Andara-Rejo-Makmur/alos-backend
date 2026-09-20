"""Backend-authoritative, frontend-safe context lifecycle projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from alos.identity import Principal


def build_context_projection(principal: Principal, *, correlation_id: str) -> dict[str, Any]:
    if not principal.active:
        return {
            "status": "DENIED",
            "denial_reason": "The authenticated principal is not active.",
            "correlation_id": correlation_id,
        }
    if not principal.scopes:
        return {
            "status": "NEEDS_INFORMATION",
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "needs_info_reason": "At least one Backend-authorized scope is required.",
            "correlation_id": correlation_id,
        }

    fingerprint = json.dumps(
        {
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "scope_refs": sorted(principal.scopes),
        },
        sort_keys=True,
    )
    context_id = f"context_{hashlib.sha256(fingerprint.encode()).hexdigest()[:24]}"
    return {
        "status": "ACTIVE",
        "context_id": context_id,
        "tenant_id": principal.tenant_id,
        "organization_id": principal.organization_id,
        "workspace_id": principal.workspace_id,
        "actor_id": principal.actor_id,
        "data_classification": "INTERNAL",
        "scope_refs": sorted(principal.scopes),
        "evidence_refs": [],
        "items": [],
        "correlation_id": correlation_id,
    }
