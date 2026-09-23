"""Append-only evidence authority backed by canonical EvidenceRef contracts."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.contracts import CanonicalContractCatalog
from alos.persistence.models import EvidenceAuthorityRecord

EVIDENCE_REF_SCHEMA = "https://schemas.alos.dev/v1/evidence/evidence-ref.schema.json"
EVIDENCE_BUNDLE_SCHEMA = "https://schemas.alos.dev/v1/evidence/evidence-bundle.schema.json"


class EvidenceConflictError(ValueError):
    pass


class EvidenceRegistry:
    def __init__(self, contracts: CanonicalContractCatalog) -> None:
        self._contracts = contracts
        self._evidence: dict[str, dict[str, Any]] = {}
        self._claim_lineage: dict[str, list[dict[str, Any]]] = {}

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated = self._contracts.validate(EVIDENCE_REF_SCHEMA, payload)
        evidence_id = str(validated["evidence_id"])
        existing = self._evidence.get(evidence_id)
        if existing is not None and existing != validated:
            raise EvidenceConflictError("immutable evidence_id already has different content")
        self._evidence[evidence_id] = copy.deepcopy(validated)
        return copy.deepcopy(validated)

    def register_claim_lineage(
        self,
        *,
        claim_id: str,
        evidence_id: str,
        source_id: str,
        retrieval_id: str | None = None,
        research_run_id: str | None = None,
        source_ref: str | None = None,
        provenance: str | None = None,
        freshness: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "source_id": source_id,
            "retrieval_id": retrieval_id,
            "research_run_id": research_run_id,
            "source_ref": source_ref,
            "provenance": provenance or "backend-authorized-retrieval",
            "freshness": freshness or "CURRENT",
            "correlation_id": correlation_id or "unknown",
            "metadata": dict(metadata or {}),
        }
        self._claim_lineage.setdefault(claim_id, []).append(payload)
        return copy.deepcopy(payload)

    def get_claim_lineage(self, claim_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._claim_lineage.get(claim_id, []))

    def verify_claim(
        self, *, claim_id: str, evidence_ids: list[str] | tuple[str, ...], minimum: int = 1
    ) -> bool:
        lineage = self._claim_lineage.get(claim_id, [])
        if len(lineage) < minimum:
            return False
        known = {item["evidence_id"] for item in lineage}
        for evidence_id in evidence_ids:
            if evidence_id in known:
                return True
        return False

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


class SqlEvidenceRegistry:
    """Persistent canonical evidence authority for multi-worker environments."""

    def __init__(
        self,
        contracts: CanonicalContractCatalog,
        session_factory: Callable[[], AsyncSession],
    ) -> None:
        self._contracts = contracts
        self._session_factory = session_factory

    async def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated = self._contracts.validate(EVIDENCE_REF_SCHEMA, payload)
        evidence_id = str(validated["evidence_id"])
        async with self._session_factory() as session:
            existing = await session.get(EvidenceAuthorityRecord, evidence_id)
            if existing is not None:
                canonical = self._canonical_from_row(existing)
                if canonical != validated:
                    raise EvidenceConflictError(
                        "immutable evidence_id already has different content"
                    )
                return canonical
            session.add(
                EvidenceAuthorityRecord(
                    evidence_id=evidence_id,
                    tenant_id=str(validated["tenant_id"]),
                    organization_id=str(validated["organization_id"]),
                    workspace_id=str(validated["workspace_id"]),
                    source_id=str(validated["source_id"]),
                    source_version=_optional_string(validated.get("source_version")),
                    uri=str(validated["uri"]),
                    content_hash=str(validated["content_hash"]),
                    anchor=_optional_string(validated.get("anchor")),
                    excerpt=_optional_string(validated.get("excerpt")),
                    data_classification=str(validated.get("data_classification", "INTERNAL")),
                    validation_status=str(validated.get("validation_status", "PENDING")),
                    metadata_payload={"canonical_ref": copy.deepcopy(validated)},
                    captured_at=datetime.fromisoformat(
                        str(validated["captured_at"]).replace("Z", "+00:00")
                    ),
                )
            )
            await session.commit()
        return copy.deepcopy(validated)

    async def get(self, evidence_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(EvidenceAuthorityRecord, evidence_id)
            if row is None:
                raise LookupError("evidence was not found")
            return self._canonical_from_row(row)

    @staticmethod
    def _canonical_from_row(row: EvidenceAuthorityRecord) -> dict[str, Any]:
        canonical = row.metadata_payload.get("canonical_ref")
        if not isinstance(canonical, dict):
            raise LookupError("evidence has no canonical persisted reference")
        return copy.deepcopy(canonical)


async def resolve_registry_result(value: Any) -> Any:
    """Await SQL-backed evidence operations while preserving the in-memory API."""

    return await value if inspect.isawaitable(value) else value


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None
