"""Backend-owned research authorization and GENESIS orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from alos.audit import AuditEvent, AuditSink, InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.identity import DataScope, Principal
from alos.integrations.genesis import GenesisClient, GenesisClientError
from alos.persistence.models import ResearchFindingRecord
from alos.research.models import ResearchFinding
from alos.security.errors import PlatformError

RESEARCH_ANALYSIS_REQUEST_SCHEMA = (
    "https://schemas.alos.dev/v1/research/research-analysis-request.schema.json"
)
RESEARCH_DECISION_SCHEMA = "https://schemas.alos.dev/v1/research/research-decision.schema.json"
RESEARCH_RECEIPT_SCHEMA = (
    "https://schemas.alos.dev/v1/research/research-request-receipt.schema.json"
)
RESEARCH_REQUEST_PERMISSION = "research.request"
EXTERNAL_RESEARCH_PERMISSION = "research.external.read"
EXTERNAL_RESEARCH_SCOPE = "scope.sources.external_read"
EXTERNAL_RESEARCH_TOOL = "research.external.retrieve"


class ResearchDomain(StrEnum):
    TECHNOLOGY = "TECHNOLOGY"
    PROPERTY_BUSINESS = "PROPERTY_BUSINESS"
    MANAGEMENT = "MANAGEMENT"
    PROPERTY_MARKET = "PROPERTY_MARKET"


class ResearchSourceMode(StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


DOMAIN_SCOPE: dict[ResearchDomain, str] = {
    ResearchDomain.TECHNOLOGY: "research.technology",
    ResearchDomain.PROPERTY_BUSINESS: "research.property_business",
    ResearchDomain.MANAGEMENT: "research.management",
    ResearchDomain.PROPERTY_MARKET: "research.property_market",
}


@dataclass(frozen=True, slots=True)
class ResearchCommand:
    question: str
    source_mode: ResearchSourceMode
    domain: ResearchDomain


def project_domain_access(principal: Principal, *, correlation_id: str) -> dict[str, Any]:
    domains: list[dict[str, Any]] = []
    for domain, required_scope in DOMAIN_SCOPE.items():
        allowed = (
            principal.active
            and RESEARCH_REQUEST_PERMISSION in principal.permissions
            and required_scope in principal.scopes
        )
        domains.append(
            {
                "domain": domain.value,
                "status": "AUTHORIZED" if allowed else "DENIED",
                "is_allowed": allowed,
                "reason": (
                    "Authorized by ALOS Backend policy."
                    if allowed
                    else "The principal lacks the required Backend permission or domain scope."
                ),
                "required_scope": required_scope,
            }
        )
    return {"domains": domains, "correlation_id": correlation_id}


@dataclass(frozen=True, slots=True)
class ResearchSourceSnapshotResult:
    status: str
    freshness: str
    cache_key: str
    source_identity: str
    effective_scope_refs: frozenset[str]
    metadata: dict[str, Any]


class ResearchSourceCacheService:
    _CLASSIFICATION_RANK: dict[str, int] = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }

    def __init__(self, *, audit: AuditSink | None = None) -> None:
        self._audit = audit or InMemoryAuditRepository()
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    @staticmethod
    def source_identity(source: Mapping[str, Any]) -> str:
        payload = {
            "source_id": str(source.get("source_id") or ""),
            "source_ref": str(source.get("source_ref") or ""),
            "source_version": str(source.get("source_version") or ""),
            "uri": str(source.get("uri") or ""),
            "content_hash": str(source.get("content_hash") or ""),
            "tenant_id": str(source.get("tenant_id") or ""),
            "workspace_id": str(source.get("workspace_id") or ""),
            "scope_refs": tuple(
                sorted(str(item) for item in (source.get("scope_refs") or ()))
            ),
            "classification": str(source.get("classification") or ""),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def resolve_snapshot(
        self,
        source: Mapping[str, Any],
        *,
        principal: Principal,
        correlation_id: str,
    ) -> ResearchSourceSnapshotResult:
        cache_key = self.source_identity(source)
        if not self._principal_may_access(source, principal):
            self._append_audit(
                "research.cache.denied",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="DENIED",
                reason="Principal lacked the required backend authorization or scope for cached research content.",
                metadata={
                    "cache_key": cache_key,
                    "source_id": str(source.get("source_id") or "unknown"),
                    "required_permissions": ["research.external.read"],
                    "required_scopes": sorted({"research.technology", "scope.sources.external_read"}),
                    "principal_permissions": sorted(principal.permissions),
                    "principal_scopes": sorted(principal.scopes),
                },
            )
            return ResearchSourceSnapshotResult(
                status="DENIED",
                freshness="UNKNOWN",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={"cache_key": cache_key, "reason": "authorization-mismatch"},
            )
        with self._lock:
            entry = self._store.get(cache_key)
        if entry is None:
            self._append_audit(
                "research.cache.miss",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="MISS",
                reason="No reusable backend snapshot was available for the requested source.",
                metadata={
                    "source_id": str(source.get("source_id") or "unknown"),
                    "source_ref": str(source.get("source_ref") or ""),
                    "cache_key": cache_key,
                    "scope_refs": sorted(str(item) for item in (source.get("scope_refs") or ())),
                },
            )
            return ResearchSourceSnapshotResult(
                status="MISS",
                freshness="UNKNOWN",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={
                    "source_id": str(source.get("source_id") or "unknown"),
                    "source_ref": str(source.get("source_ref") or ""),
                    "cache_key": cache_key,
                    "correlation_id": correlation_id,
                    "retrieval_required": True,
                },
            )

        if entry["tenant_id"] != principal.tenant_id:
            self._append_audit(
                "research.cache.denied",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="DENIED",
                reason="Tenant mismatch prevented cache reuse.",
                metadata={"cache_key": cache_key, "source_tenant": entry["tenant_id"]},
            )
            return ResearchSourceSnapshotResult(
                status="DENIED",
                freshness="UNKNOWN",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={"cache_key": cache_key, "reason": "tenant-mismatch"},
            )

        if entry["workspace_id"] != principal.workspace_id:
            self._append_audit(
                "research.cache.denied",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="DENIED",
                reason="Workspace mismatch prevented cache reuse.",
                metadata={"cache_key": cache_key, "source_workspace": entry["workspace_id"]},
            )
            return ResearchSourceSnapshotResult(
                status="DENIED",
                freshness="UNKNOWN",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={"cache_key": cache_key, "reason": "workspace-mismatch"},
            )

        required_scope = set(entry["scope_refs"])
        if not required_scope.issubset(principal.scopes):
            self._append_audit(
                "research.cache.denied",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="DENIED",
                reason="Scope mismatch prevented cache reuse.",
                metadata={
                    "cache_key": cache_key,
                    "required_scope": sorted(required_scope),
                    "principal_scope": sorted(principal.scopes),
                },
            )
            return ResearchSourceSnapshotResult(
                status="DENIED",
                freshness="UNKNOWN",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={"cache_key": cache_key, "reason": "scope-mismatch"},
            )

        if not self._classification_allowed(entry["classification"], principal):
            self._append_audit(
                "research.cache.denied",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="DENIED",
                reason="Classification mismatch prevented cache reuse.",
                metadata={
                    "cache_key": cache_key,
                    "stored_classification": entry["classification"],
                    "principal_scope": sorted(principal.scopes),
                },
            )
            return ResearchSourceSnapshotResult(
                status="DENIED",
                freshness="UNKNOWN",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={"cache_key": cache_key, "reason": "classification-mismatch"},
            )

        freshness = entry.get("freshness", "CURRENT")
        expires_at = _parse_datetime(entry.get("expires_at"))
        now = datetime.now(UTC)
        if expires_at is not None and expires_at <= now:
            freshness = "STALE"

        if freshness == "STALE":
            self._append_audit(
                "research.cache.stale",
                entity_id=cache_key,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                correlation_id=correlation_id,
                outcome="STALE",
                reason="Cached source snapshot exceeded freshness policy and was not reused.",
                metadata={
                    "cache_key": cache_key,
                    "source_id": entry.get("source_id"),
                    "freshness": "STALE",
                    "expires_at": entry.get("expires_at"),
                },
            )
            return ResearchSourceSnapshotResult(
                status="STALE",
                freshness="STALE",
                cache_key=cache_key,
                source_identity=cache_key,
                effective_scope_refs=frozenset(principal.scopes),
                metadata={
                    "cache_key": cache_key,
                    "source_id": entry.get("source_id"),
                    "source_ref": entry.get("source_ref"),
                    "freshness": "STALE",
                    "correlation_id": correlation_id,
                },
            )

        self._append_audit(
            "research.cache.hit",
            entity_id=cache_key,
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            correlation_id=correlation_id,
            outcome="HIT",
            reason="Reusable cached snapshot passed backend authorization and freshness policy.",
            metadata={
                "cache_key": cache_key,
                "source_id": entry.get("source_id"),
                "freshness": freshness,
                "scope_refs": sorted(principal.scopes),
            },
        )
        return ResearchSourceSnapshotResult(
            status="HIT",
            freshness=freshness,
            cache_key=cache_key,
            source_identity=cache_key,
            effective_scope_refs=frozenset(principal.scopes),
            metadata={
                "cache_key": cache_key,
                "source_id": entry.get("source_id"),
                "source_ref": entry.get("source_ref"),
                "classification": entry.get("classification"),
                "provenance": entry.get("provenance"),
                "freshness": freshness,
                "correlation_id": correlation_id,
                "retrieval_timestamp": entry.get("retrieved_at"),
            },
        )

    def store_snapshot(
        self,
        source: Mapping[str, Any],
        *,
        principal: Principal,
        correlation_id: str,
    ) -> ResearchSourceSnapshotResult:
        cache_key = self.source_identity(source)
        self._require_authorization(source, principal=principal)
        now = datetime.now(UTC)
        retrieved_at = _parse_datetime(source.get("retrieved_at")) or now
        expires_at = _parse_datetime(source.get("expires_at"))
        freshness = "CURRENT" if expires_at is None or expires_at > now else "STALE"
        metadata = {
            "source_id": str(source.get("source_id") or "unknown"),
            "source_ref": str(source.get("source_ref") or ""),
            "source_version": str(source.get("source_version") or ""),
            "uri": str(source.get("uri") or ""),
            "content_hash": str(source.get("content_hash") or ""),
            "classification": str(source.get("classification") or "INTERNAL"),
            "scope_refs": sorted(str(item) for item in (source.get("scope_refs") or ())),
            "tenant_id": str(source.get("tenant_id") or principal.tenant_id),
            "workspace_id": str(source.get("workspace_id") or principal.workspace_id),
            "organization_id": str(source.get("organization_id") or principal.organization_id),
            "actor_id": principal.actor_id,
            "provenance": str(
                (source.get("retrieval_metadata") or {}).get("provenance")
                or "backend-authorized-retrieval"
            ),
            "correlation_id": correlation_id,
            "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z") if expires_at else None,
            "freshness": freshness,
            "cache_key": cache_key,
        }
        entry = {
            **metadata,
            "scope_refs": tuple(sorted(str(item) for item in (source.get("scope_refs") or ()))),
            "classification": metadata["classification"],
            "freshness": freshness,
            "retrieved_at": metadata["retrieved_at"],
            "expires_at": metadata["expires_at"],
            "source_ref": metadata["source_ref"],
        }
        with self._lock:
            self._store[cache_key] = entry
        self._append_audit(
            "research.cache.stored",
            entity_id=cache_key,
            actor_id=principal.actor_id,
            tenant_id=str(source.get("tenant_id") or principal.tenant_id),
            organization_id=str(source.get("organization_id") or principal.organization_id),
            workspace_id=str(source.get("workspace_id") or principal.workspace_id),
            correlation_id=correlation_id,
            outcome="CACHED",
            reason="Backend-authorized source snapshot was persisted for reuse.",
            metadata={
                "cache_key": cache_key,
                "source_id": metadata["source_id"],
                "freshness": freshness,
                "source_ref": metadata["source_ref"],
                "scope_refs": metadata["scope_refs"],
            },
        )
        return ResearchSourceSnapshotResult(
            status="CACHED",
            freshness=freshness,
            cache_key=cache_key,
            source_identity=cache_key,
            effective_scope_refs=frozenset(principal.scopes),
            metadata=metadata,
        )

    def _require_authorization(self, source: Mapping[str, Any], *, principal: Principal) -> None:
        if not self._principal_may_access(source, principal):
            raise ValueError("cache access requires authorized research scope and permissions")

    def _principal_may_access(self, source: Mapping[str, Any], principal: Principal) -> bool:
        if not principal.active:
            return False
        if "research.external.read" not in principal.permissions:
            return False
        required_scopes = {"research.technology", "scope.sources.external_read"}
        if not required_scopes.intersection(principal.scopes):
            return False
        if str(source.get("tenant_id") or principal.tenant_id) != principal.tenant_id:
            return False
        if str(source.get("workspace_id") or principal.workspace_id) != principal.workspace_id:
            return False
        scope_refs = set(str(item) for item in (source.get("scope_refs") or ()))
        if not scope_refs.issubset(principal.scopes):
            return False
        classification = str(source.get("classification") or "INTERNAL")
        if not self._classification_allowed(classification, principal):
            return False
        return True

    @staticmethod
    def _classification_allowed(classification: str, principal: Principal) -> bool:
        rank = ResearchSourceCacheService._CLASSIFICATION_RANK.get(classification, 99)
        if principal.data_scope is DataScope.OWN_ASSIGNED:
            return rank <= 1
        if principal.data_scope is DataScope.PROJECT:
            return rank <= 1
        if principal.data_scope is DataScope.DIVISION:
            return rank <= 2
        return rank <= 3

    def _append_audit(
        self,
        event_type: str,
        *,
        entity_id: str,
        actor_id: str,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        correlation_id: str,
        outcome: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            entity_type="research_cache",
            entity_id=entity_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            reason=reason,
            metadata=metadata,
        )
        result = self._audit.append(event)
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                asyncio.create_task(result)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


class SqlResearchFindingStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, finding: ResearchFinding, *, actor_id: str, correlation_id: str | None = None) -> ResearchFinding:
        with self._session_factory() as session:
            row = session.get(ResearchFindingRecord, finding.finding_id)
            if row is not None:
                raise ValueError("duplicate research finding")
            session.add(
                ResearchFindingRecord(
                    finding_id=finding.finding_id,
                    tenant_id="unknown",
                    organization_id="unknown",
                    workspace_id="unknown",
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    kind=finding.kind.value,
                    domain=finding.domain.value,
                    statement=finding.statement,
                    evidence_refs=list(finding.evidence_refs),
                    confidence=float(finding.confidence),
                    source_ref=finding.source_ref,
                    retrieval_metadata=finding.retrieval_metadata,
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()
            return finding

    def get(self, finding_id: str) -> ResearchFinding:
        with self._session_factory() as session:
            row = session.get(ResearchFindingRecord, finding_id)
            if row is None:
                raise ValueError("research finding was not found")
            return self._from_row(row)

    def list(self) -> tuple[ResearchFinding, ...]:
        with self._session_factory() as session:
            rows = session.scalars(select(ResearchFindingRecord)).all()
            return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: ResearchFindingRecord) -> ResearchFinding:
        from alos.research.models import FindingKind, ResearchDomain

        return ResearchFinding(
            finding_id=row.finding_id,
            kind=FindingKind(row.kind),
            domain=ResearchDomain(row.domain),
            statement=row.statement,
            evidence_refs=tuple(row.evidence_refs or []),
            confidence=float(row.confidence),
            source_ref=row.source_ref,
            retrieval_metadata=dict(row.retrieval_metadata or {}),
        )


class ResearchFindingService:
    def __init__(self, *, session_factory: sessionmaker[Session] | None = None) -> None:
        self._store = SqlResearchFindingStore(session_factory) if session_factory is not None else None

    def create(
        self,
        finding: ResearchFinding,
        *,
        actor_id: str,
        correlation_id: str | None = None,
    ) -> ResearchFinding:
        if not finding.finding_id:
            raise ValueError("missing finding id")
        if not finding.evidence_refs:
            raise ValueError("missing evidence")
        if self._store is not None:
            return self._store.create(finding, actor_id=actor_id, correlation_id=correlation_id)
        return finding

    def get(self, finding_id: str) -> ResearchFinding:
        if self._store is None:
            raise ValueError("research finding store was not configured")
        return self._store.get(finding_id)

    def list(self) -> tuple[ResearchFinding, ...]:
        if self._store is None:
            return ()
        return self._store.list()


class ResearchService:
    def __init__(
        self,
        *,
        contracts: CanonicalContractCatalog,
        genesis: GenesisClient,
        audit: AuditSink,
        tool_executor: Any | None = None,
    ) -> None:
        self._contracts = contracts
        self._genesis = genesis
        self._audit = audit
        self._tool_executor = tool_executor

    async def request(
        self,
        command: ResearchCommand,
        *,
        principal: Principal,
        correlation_id: str,
    ) -> dict[str, Any]:
        required_scope = DOMAIN_SCOPE[command.domain]
        if (
            not principal.active
            or RESEARCH_REQUEST_PERMISSION not in principal.permissions
            or required_scope not in principal.scopes
        ):
            await self._audit.append(
                self._event(
                    principal,
                    correlation_id=correlation_id,
                    domain=command.domain,
                    outcome="DENIED",
                    decision="SCOPE_DENIED",
                )
            )
            raise PlatformError(
                "RESEARCH_SCOPE_DENIED",
                "The principal is not authorized for the requested research domain.",
                status_code=403,
                correlation_id=correlation_id,
            )

        allowed_tools: list[str] = []
        permission_refs = [RESEARCH_REQUEST_PERMISSION]
        scope_refs = [required_scope]
        if (
            command.source_mode is ResearchSourceMode.EXTERNAL
            and EXTERNAL_RESEARCH_PERMISSION in principal.permissions
            and EXTERNAL_RESEARCH_SCOPE in principal.scopes
        ):
            allowed_tools.append(EXTERNAL_RESEARCH_TOOL)
            permission_refs.append(EXTERNAL_RESEARCH_PERMISSION)
            scope_refs.append(EXTERNAL_RESEARCH_SCOPE)
            if self._tool_executor is not None:
                tool_request = {
                    "tool_call_id": f"toolcall_{correlation_id}",
                    "run_id": f"run_{correlation_id}",
                    "tool_id": EXTERNAL_RESEARCH_TOOL,
                    "execution_context": {
                        "tenant_id": principal.tenant_id,
                        "organization_id": principal.organization_id,
                        "workspace_id": principal.workspace_id,
                        "actor_id": principal.actor_id,
                        "permission_refs": [EXTERNAL_RESEARCH_PERMISSION],
                        "scope_refs": [EXTERNAL_RESEARCH_SCOPE],
                        "correlation_id": correlation_id,
                    },
                    "arguments": {
                        "research_request": command.question,
                        "domain": command.domain.value.lower(),
                        "source_requirement": "approved.public-source",
                        "actor": principal.actor_id,
                        "scope": EXTERNAL_RESEARCH_SCOPE,
                        "correlation_id": correlation_id,
                        "budget_limit": 1.0,
                    },
                }
                tool_outcome = await self._tool_executor.execute(
                    tool_request,
                    principal=principal,
                    transport_correlation_id=correlation_id,
                )
                if tool_outcome.correlation_id != correlation_id:
                    raise PlatformError(
                        "RESEARCH_TOOL_CORRELATION_MISMATCH",
                        "External research ToolExecutor correlation does not match the request.",
                        status_code=502,
                        correlation_id=correlation_id,
                    )
                if tool_outcome.result.get("status") != "SUCCESS":
                    raise PlatformError(
                        "RESEARCH_TOOL_DENIED",
                        "The backend ToolExecutor denied the external retrieval path.",
                        status_code=403,
                        correlation_id=correlation_id,
                    )

        role_refs = sorted(principal.roles)
        payload: dict[str, Any] = {
            "question": command.question,
            "source_mode": command.source_mode.value,
            "domain": command.domain.value,
            "execution_context": {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "actor_id": principal.actor_id,
                "authority_context": {
                    "role": role_refs[0] if role_refs else "AUTHENTICATED_USER",
                    "role_refs": role_refs,
                    "authority_level": "REQUESTER",
                },
                "permission_refs": permission_refs,
                "allowed_tool_ids": allowed_tools,
                "scope_refs": scope_refs,
                "data_classification": "INTERNAL",
                "correlation_id": correlation_id,
                "execution_budget": {
                    "max_cost": 0,
                    "max_tool_calls": 1 if allowed_tools else 0,
                    "timeout_seconds": 30,
                },
            },
        }
        try:
            validated_request = self._contracts.validate(
                RESEARCH_ANALYSIS_REQUEST_SCHEMA, payload
            )
            decision = await self._genesis.research(
                validated_request,
                correlation_id=correlation_id,
            )
            validated_decision = self._contracts.validate(RESEARCH_DECISION_SCHEMA, decision)
        except ContractValidationError as exc:
            raise PlatformError(
                "RESEARCH_CONTRACT_INVALID",
                "Research boundary payload failed canonical contract validation.",
                status_code=502,
                correlation_id=correlation_id,
                details={"path": exc.path, "reason": exc.reason},
            ) from exc
        except GenesisClientError as exc:
            status_code = 503 if exc.code in {"GENESIS_TIMEOUT", "GENESIS_UNAVAILABLE"} else 502
            raise PlatformError(
                exc.code,
                exc.message,
                status_code=status_code,
                retryable=exc.retryable,
                correlation_id=correlation_id,
            ) from exc

        if validated_decision["correlation_id"] != correlation_id:
            raise PlatformError(
                "RESEARCH_CORRELATION_MISMATCH",
                "GENESIS research decision correlation does not match the Backend request.",
                status_code=502,
                correlation_id=correlation_id,
            )
        decision_kind = str(validated_decision["decision"])
        state = (
            "NEEDS_REVIEW"
            if decision_kind
            in {"REQUEST_EXTERNAL_RESEARCH", "INSUFFICIENT_EVIDENCE", "NEEDS_INFORMATION"}
            else "RECEIVED"
        )
        request_id = f"research_{hashlib.sha256(correlation_id.encode()).hexdigest()[:24]}"
        receipt = {
            "request_id": request_id,
            "state": state,
            "correlation_id": correlation_id,
            "decision": decision_kind,
        }
        validated_receipt = self._contracts.validate(RESEARCH_RECEIPT_SCHEMA, receipt)
        await self._audit.append(
            self._event(
                principal,
                correlation_id=correlation_id,
                domain=command.domain,
                outcome="SUCCESS",
                decision=decision_kind,
            )
        )
        return validated_receipt

    @staticmethod
    def _event(
        principal: Principal,
        *,
        correlation_id: str,
        domain: ResearchDomain,
        outcome: str,
        decision: str,
    ) -> AuditEvent:
        entity_id = f"research_{hashlib.sha256(correlation_id.encode()).hexdigest()[:24]}"
        return AuditEvent(
            event_type="research.request",
            entity_type="research_request",
            entity_id=entity_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            reason="Backend-governed research decision",
            metadata={"domain": domain.value, "decision": decision},
        )
