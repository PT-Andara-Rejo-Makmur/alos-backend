"""Append-only evidence authority backed by canonical EvidenceRef contracts."""

from __future__ import annotations

import copy
from typing import Any

from alos.contracts import CanonicalContractCatalog

EVIDENCE_REF_SCHEMA = "https://schemas.alos.dev/v1/evidence/evidence-ref.schema.json"
EVIDENCE_BUNDLE_SCHEMA = "https://schemas.alos.dev/v1/evidence/evidence-bundle.schema.json"


class EvidenceConflictError(ValueError):
    pass


class EvidenceRegistry:
    def __init__(self, contracts: CanonicalContractCatalog) -> None:
        self._contracts = contracts
        self._evidence: dict[str, dict[str, Any]] = {}

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated = self._contracts.validate(EVIDENCE_REF_SCHEMA, payload)
        evidence_id = str(validated["evidence_id"])
        existing = self._evidence.get(evidence_id)
        if existing is not None and existing != validated:
            raise EvidenceConflictError("immutable evidence_id already has different content")
        self._evidence[evidence_id] = copy.deepcopy(validated)
        return copy.deepcopy(validated)

    def bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated = self._contracts.validate(EVIDENCE_BUNDLE_SCHEMA, payload)
        tenant_id = str(validated["tenant_id"])
        organization_id = str(validated["organization_id"])
        workspace_id = str(validated["workspace_id"])
        for evidence in validated["evidence_refs"]:
            if evidence.get("tenant_id") not in {None, tenant_id}:
                raise EvidenceConflictError("evidence tenant does not match bundle")
            if evidence.get("organization_id") not in {None, organization_id}:
                raise EvidenceConflictError("evidence organization does not match bundle")
            if evidence.get("workspace_id") not in {None, workspace_id}:
                raise EvidenceConflictError("evidence workspace does not match bundle")
            self.register(evidence)
        return copy.deepcopy(validated)

    def get(self, evidence_id: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(self._evidence[evidence_id])
        except KeyError as exc:
            raise LookupError("evidence was not found") from exc
