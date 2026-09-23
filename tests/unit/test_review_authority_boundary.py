from __future__ import annotations

import copy
from typing import Any

import pytest

from alos.api.public.routes import (
    _require_authoritative_review_snapshot,
    _require_review_package_matches_invocation,
)
from alos.registry_contracts import RegistryAuthorityView
from alos.security.errors import PlatformError


class _EvidenceRegistry:
    def __init__(self, evidence: dict[str, Any]) -> None:
        self._evidence = evidence

    def get(self, evidence_id: str) -> dict[str, Any]:
        if evidence_id != self._evidence["evidence_id"]:
            raise LookupError("evidence was not found")
        return copy.deepcopy(self._evidence)


def _review_fixture() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], RegistryAuthorityView, dict[str, Any]
]:
    evidence = {
        "tenant_id": "tenant.review.001",
        "organization_id": "org.review.001",
        "workspace_id": "workspace.review.001",
        "correlation_id": "corr.review.001",
        "scope_refs": ["scope.diagnostic"],
        "evidence_id": "evidence.review.001",
        "source_id": "source.review.001",
        "uri": "urn:alos:review:evidence:001",
        "captured_at": "2026-09-23T10:00:00Z",
        "content_hash": "sha256:" + "b" * 64,
        "data_classification": "INTERNAL",
        "instruction_authority": False,
        "validation_status": "VALID",
        "metadata": {
            "review_subject": {
                "subject_id": "agent.runtime.diagnostic",
                "subject_version": "1.0.0",
            }
        },
    }
    agent_payload = {
        "name": "Runtime Diagnostic",
        "purpose": "Validate the governed Backend and GENESIS runtime boundary.",
        "capability_ids": ["capability.runtime.diagnostic"],
        "skill_refs": [],
        "model_policy_ref": "policy.runtime-test",
        "execution_budget": {"max_tokens": 100, "max_steps": 3},
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }
    authority = RegistryAuthorityView(
        subject_type="agent",
        subject_id="agent.runtime.diagnostic",
        version="1.0.0",
        lifecycle="ACTIVE",
        owner="actor.review.001",
        risk="LOW",
        tools=("diagnostic.echo",),
        permissions=("tools.diagnostic.execute",),
        scope=("scope.diagnostic",),
        tenant_id="tenant.review.001",
        organization_id="org.review.001",
        workspace_id="workspace.review.001",
        digest="sha256:" + "a" * 64,
    )
    subject = {
        "review_id": "review.authority.001",
        "subject_id": authority.subject_id,
        "subject_version": authority.version,
        "tenant_id": authority.tenant_id,
        "organization_id": authority.organization_id,
        "workspace_id": authority.workspace_id,
        "correlation_id": "corr.review.001",
        "purpose": agent_payload["purpose"],
        "materiality": "NON_MATERIAL",
        "business_context": {
            "registry_digest": authority.digest,
            "subject_type": "agent",
        },
        "capability": {
            "capability_id": "capability.runtime.diagnostic",
            "version": "1.0.0",
            "name": agent_payload["name"],
            "purpose": agent_payload["purpose"],
            "owner": authority.owner,
            "capability_type": "AGENT",
            "output_state": "NEEDS_REVIEW",
            "lifecycle_state": "DRAFT",
            "scope_refs": ["scope.diagnostic"],
            "tool_ids": ["diagnostic.echo"],
            "permission_refs": ["tools.diagnostic.execute"],
            "risk_level": "LOW",
        },
        "scope": ["scope.diagnostic"],
        "permissions": ["tools.diagnostic.execute"],
        "skills": [],
        "tools": [
            {
                "tool_id": "diagnostic.echo",
                "purpose": "Backend-authorized tool: diagnostic.echo",
                "backend_executor": True,
            }
        ],
        "model_policy": {"gateway_required": True, "policy_ref": "policy.runtime-test"},
        "delegation_policy": {
            "enabled": False,
            "lineage_required": True,
            "max_depth": 0,
        },
        "execution_budget": {"max_tokens": 100, "max_steps": 3},
        "evidence_refs": [evidence],
    }
    evaluation_subject = {
        "subject_id": authority.subject_id,
        "subject_version": authority.version,
        "tenant_id": authority.tenant_id,
        "organization_id": authority.organization_id,
        "workspace_id": authority.workspace_id,
        "correlation_id": "corr.review.001",
        "observations": {},
    }
    return subject, evaluation_subject, agent_payload, authority, evidence


def _validate(subject: dict[str, Any], evaluation_subject: dict[str, Any]) -> None:
    _, _, agent_payload, authority, evidence = _review_fixture()
    _require_authoritative_review_snapshot(
        subject=subject,
        evaluation_subject=evaluation_subject,
        agent_payload=agent_payload,
        authority=authority,
        authoritative_evidence={
            evidence["evidence_id"]: _EvidenceRegistry(evidence).get(evidence["evidence_id"])
        },
        skill_registry=None,
    )


def test_review_accepts_exact_backend_authorized_snapshot() -> None:
    subject, evaluation_subject, _, _, _ = _review_fixture()

    _validate(subject, evaluation_subject)


@pytest.mark.parametrize(
    "tampering",
    ["scope", "tool", "tool_purpose", "budget", "evidence", "observation"],
)
def test_review_rejects_caller_controlled_authority_facts(tampering: str) -> None:
    subject, evaluation_subject, _, _, _ = _review_fixture()
    if tampering == "scope":
        subject["scope"] = ["scope.admin"]
    elif tampering == "tool":
        subject["tools"][0]["tool_id"] = "admin.unauthorized"
    elif tampering == "tool_purpose":
        subject["tools"][0]["purpose"] = "Caller-controlled description"
    elif tampering == "budget":
        subject["execution_budget"] = {"max_tokens": 1000000, "max_steps": 1000}
    elif tampering == "evidence":
        subject["evidence_refs"][0]["content_hash"] = "sha256:" + "c" * 64
    else:
        evaluation_subject["observations"] = {
            "assurance.runtime.budget_enforced": {
                "status": "PASS",
                "details": "caller supplied",
            }
        }

    with pytest.raises(PlatformError) as captured:
        _validate(subject, evaluation_subject)

    assert captured.value.status_code == 403
    assert captured.value.code in {
        "REVIEW_FACTS_NOT_AUTHORITATIVE",
        "REVIEW_EVIDENCE_NOT_AUTHORITATIVE",
    }


def test_review_rejects_genesis_snapshot_mutation() -> None:
    subject, evaluation_subject, _, _, _ = _review_fixture()
    package = {
        "identity": {
            "review_id": subject["review_id"],
            "tenant_id": subject["tenant_id"],
            "organization_id": subject["organization_id"],
            "workspace_id": subject["workspace_id"],
            "correlation_id": subject["correlation_id"],
            "subject_id": subject["subject_id"],
            "subject_version": subject["subject_version"],
        },
        **{
            key: copy.deepcopy(value)
            for key, value in subject.items()
            if key
            not in {
                "review_id",
                "subject_id",
                "subject_version",
                "tenant_id",
                "organization_id",
                "workspace_id",
                "correlation_id",
            }
        },
    }
    invocation = {"subject": subject, "evaluation_subject": evaluation_subject}
    _require_review_package_matches_invocation(package=package, invocation=invocation)

    package["scope"] = ["scope.admin"]
    with pytest.raises(PlatformError) as captured:
        _require_review_package_matches_invocation(package=package, invocation=invocation)

    assert captured.value.code == "GENESIS_REVIEW_SNAPSHOT_MISMATCH"
    assert captured.value.status_code == 502


@pytest.mark.parametrize("tampering", ["invalid_status", "unrelated_subject"])
def test_review_rejects_registered_but_inadmissible_evidence(tampering: str) -> None:
    subject, evaluation_subject, agent_payload, authority, evidence = _review_fixture()
    if tampering == "invalid_status":
        evidence["validation_status"] = "INVALID"
    else:
        evidence["metadata"]["review_subject"] = {
            "subject_id": "agent.unrelated",
            "subject_version": "9.9.9",
        }
    subject["evidence_refs"] = [copy.deepcopy(evidence)]

    with pytest.raises(PlatformError) as captured:
        _require_authoritative_review_snapshot(
            subject=subject,
            evaluation_subject=evaluation_subject,
            agent_payload=agent_payload,
            authority=authority,
            authoritative_evidence={evidence["evidence_id"]: copy.deepcopy(evidence)},
            skill_registry=None,
        )

    assert captured.value.code == "REVIEW_EVIDENCE_NOT_AUTHORITATIVE"
    assert captured.value.status_code == 403
