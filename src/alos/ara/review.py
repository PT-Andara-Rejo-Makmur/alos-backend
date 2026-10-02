"""Advisory review through the existing GENESIS boundary and captured proposal evidence."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from alos.context.policy import ARA_BUDGET, maximum_classification
from alos.contracts import CanonicalContractCatalog
from alos.evidence import resolve_registry_result
from alos.identity import Principal
from alos.integrations.genesis import GenesisClient


async def review_proposal(
    *,
    proposal: dict[str, Any],
    principal: Principal,
    run_id: str,
    correlation_id: str,
    genesis: GenesisClient,
    contracts: CanonicalContractCatalog,
    evidence_registry: Any,
    proposal_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured = {"proposal": proposal, "context": proposal_context or {}}
    digest = hashlib.sha256(json.dumps(captured, sort_keys=True).encode()).hexdigest()
    boundary = {
        key: getattr(principal, key) for key in ("tenant_id", "organization_id", "workspace_id")
    }
    evidence = {
        **boundary,
        "evidence_id": f"evidence_{proposal['proposal_id']}",
        "source_id": f"source_{proposal['proposal_id']}",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "uri": f"urn:alos:ara:proposal:{proposal['proposal_id']}",
        "captured_at": datetime.now(UTC).isoformat(),
        "content_hash": f"sha256:{digest}",
        "data_classification": maximum_classification(principal),
        "scope_refs": sorted(principal.scopes),
        "source_type": "INTERNAL",
        "freshness": "CURRENT",
        "validation_status": "VALID",
        "instruction_authority": False,
        "content_trust": "UNTRUSTED",
        "metadata": {"source_kind": "ACTION_PROPOSAL", "business_truth": False},
    }
    evidence = await resolve_registry_result(evidence_registry.register(evidence))
    capability = {
        **boundary,
        "correlation_id": correlation_id,
        "capability_id": "business.action_proposal",
        "version": "1.0.0",
        "name": "ARA action proposal",
        "purpose": "Prepare a bounded proposal for a human decision.",
        "owner": principal.actor_id,
        "capability_type": "HUMAN_TASK",
        "output_state": "NEEDS_REVIEW",
        "lifecycle_state": "DRAFT",
        "scope_refs": sorted(principal.scopes),
        "permission_refs": [],
        "tool_ids": [],
        "human_gate_required": True,
        "constraints": ["No autonomous business mutation."],
    }
    identity = {
        **boundary,
        "correlation_id": correlation_id,
        "subject_id": proposal["proposal_id"],
        "subject_version": "1.0.0",
    }
    payload = {
        "subject": {
            **identity,
            "review_id": f"review_{proposal['proposal_id']}",
            "purpose": "Review the ARA proposal; AI recommendation is advisory.",
            "materiality": "MATERIAL" if proposal["kind"] == "MATERIAL_ACTION" else "NON_MATERIAL",
            "business_context": {"run_id": run_id, **captured},
            "capability": capability,
            "scope": sorted(principal.scopes),
            "permissions": [],
            "skills": [],
            "tools": [],
            "model_policy": {"gateway_required": True, "policy_ref": "ara.deterministic"},
            "delegation_policy": {"enabled": False, "lineage_required": True, "max_depth": 0},
            "execution_budget": ARA_BUDGET,
            "evidence_refs": [evidence],
        },
        "evaluation_subject": {**identity, "observations": {}},
    }
    contracts.validate("https://schemas.alos.dev/v1/review/review-invocation.schema.json", payload)
    result = await genesis.review(payload, correlation_id=correlation_id)
    result = contracts.validate(
        "https://schemas.alos.dev/v1/review/review-package.schema.json", result
    )
    if any(result["identity"].get(key) != value for key, value in identity.items()):
        raise ValueError("Review identity mismatch")
    if "it_decision" in result or "director_decision" in result:
        raise ValueError("Advisory review cannot issue human decisions")
    return result
