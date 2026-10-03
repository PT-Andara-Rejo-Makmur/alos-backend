"""Build the existing advisory review contract from an exact authorized registry version."""

from typing import Any
from uuid import uuid4

from alos.identity import Principal
from alos.registry import RegistryEntry
from alos.registry_contracts import RegistryAuthorityView


def review_invocation(
    entry: RegistryEntry, principal: Principal, correlation_id: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    authority = RegistryAuthorityView.from_entry(entry).authorize(principal)
    payload = entry.payload
    identity = {
        "subject_id": entry.subject_id,
        "subject_version": entry.version,
        "tenant_id": principal.tenant_id,
        "organization_id": principal.organization_id,
        "workspace_id": principal.workspace_id,
        "correlation_id": correlation_id,
    }
    capability = {
        "capability_id": payload["capability_ids"][0],
        "version": entry.version,
        "name": payload["name"],
        "purpose": payload["purpose"],
        "owner": authority.owner,
        "capability_type": "AGENT",
        "output_state": "NEEDS_REVIEW",
        "lifecycle_state": "DRAFT",
        "scope_refs": list(authority.scope),
        "tool_ids": list(authority.tools),
        "permission_refs": list(authority.permissions),
        "risk_level": authority.risk,
    }
    return {
        "subject": {
            **identity,
            "review_id": "review_" + uuid4().hex,
            "purpose": payload["purpose"],
            "materiality": "MATERIAL" if authority.risk in {"HIGH", "CRITICAL"} else "NON_MATERIAL",
            "business_context": {"registry_digest": authority.digest, "subject_type": "agent"},
            "capability": capability,
            "scope": list(authority.scope),
            "permissions": list(authority.permissions),
            "skills": [],
            "tools": [
                {
                    "tool_id": tool,
                    "purpose": "Backend-authorized tool: " + tool,
                    "backend_executor": True,
                }
                for tool in authority.tools
            ],
            "model_policy": {"gateway_required": True, "policy_ref": payload["model_policy_ref"]},
            "delegation_policy": {
                "enabled": payload["delegation_policy"]["enabled"],
                "lineage_required": True,
                "max_depth": payload["delegation_policy"]["max_depth"],
            },
            "execution_budget": payload["execution_budget"],
            "evidence_refs": [evidence],
        },
        "evaluation_subject": {**identity, "observations": {}},
    }
