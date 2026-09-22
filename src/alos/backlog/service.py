"""Backend-owned backlog candidate creation from an approved recommendation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from alos.audit import AuditEvent, AuditSink
from alos.persistence.models import BacklogCandidateRecord
from alos.research.models import (
    BacklogCandidate,
    BacklogCandidateState,
    ResearchFinding,
    ResearchRecommendation,
)


@dataclass(frozen=True, slots=True)
class BacklogCandidateRequest:
    recommendation: ResearchRecommendation
    finding: ResearchFinding
    actor_id: str
    scope_ref: str
    correlation_id: str
    evidence_refs: Sequence[str] | None = None
    priority_suggestion: str | None = None
    owner_suggestion: str | None = None


class SqlBacklogCandidateStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, candidate: BacklogCandidate) -> BacklogCandidate:
        with self._session_factory() as session:
            row = session.get(BacklogCandidateRecord, candidate.candidate_id)
            if row is not None:
                raise ValueError("duplicate backlog candidate")
            session.add(self._to_row(candidate))
            session.commit()
            return candidate

    def get(self, candidate_id: str) -> BacklogCandidate:
        with self._session_factory() as session:
            row = session.get(BacklogCandidateRecord, candidate_id)
            if row is None:
                raise ValueError("backlog candidate was not found")
            return self._from_row(row)

    @staticmethod
    def _to_row(candidate: BacklogCandidate) -> BacklogCandidateRecord:
        return BacklogCandidateRecord(
            candidate_id=candidate.candidate_id,
            finding_id=candidate.finding_id,
            recommendation_id=candidate.recommendation_id,
            impact=candidate.impact,
            priority_suggestion=candidate.priority_suggestion,
            owner_suggestion=candidate.owner_suggestion,
            evidence_refs=list(candidate.evidence_refs),
            approval_state=candidate.approval_state.value,
            actor_id=candidate.actor_id,
            scope_ref=candidate.scope_ref,
            correlation_id=candidate.correlation_id,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _from_row(row: BacklogCandidateRecord) -> BacklogCandidate:
        return BacklogCandidate(
            candidate_id=row.candidate_id,
            finding_id=row.finding_id,
            recommendation_id=row.recommendation_id,
            impact=row.impact,
            priority_suggestion=row.priority_suggestion,
            owner_suggestion=row.owner_suggestion,
            evidence_refs=tuple(row.evidence_refs or []),
            approval_state=BacklogCandidateState(row.approval_state),
            actor_id=row.actor_id,
            scope_ref=row.scope_ref,
            correlation_id=row.correlation_id,
        )


class BacklogCandidateService:
    """Create draft backlog candidates only. No auto-execution or production mutation."""

    def __init__(self, *, audit: AuditSink | None = None, session_factory: sessionmaker[Session] | None = None) -> None:
        self._audit = audit
        self._seen: set[str] = set()
        self._store = SqlBacklogCandidateStore(session_factory) if session_factory is not None else None

    def create(self, request: BacklogCandidateRequest) -> BacklogCandidate:
        if not request.recommendation.recommendation_id:
            raise ValueError("missing recommendation")
        if not request.finding.finding_id:
            raise ValueError("missing finding")
        if not request.recommendation.evidence_refs:
            raise ValueError("missing evidence")
        if request.scope_ref != "scope.sources.external_read" and request.scope_ref != "research.technology":
            raise ValueError("invalid scope")
        candidate_id = f"candidate_{request.recommendation.recommendation_id}"
        if request.actor_id == "agent" or request.actor_id.startswith("agent_"):
            raise ValueError("agent cannot self-promote to production backlog")
        if candidate_id in self._seen:
            raise ValueError("duplicate backlog candidate")
        candidate = BacklogCandidate(
            candidate_id=candidate_id,
            finding_id=request.finding.finding_id,
            recommendation_id=request.recommendation.recommendation_id,
            impact=request.recommendation.impact,
            priority_suggestion=request.priority_suggestion or request.recommendation.priority_suggestion,
            owner_suggestion=request.owner_suggestion or request.recommendation.owner_suggestion,
            evidence_refs=tuple(request.evidence_refs or request.recommendation.evidence_refs),
            approval_state=BacklogCandidateState.DRAFT,
            actor_id=request.actor_id,
            scope_ref=request.scope_ref,
            correlation_id=request.correlation_id,
        )
        self._seen.add(candidate_id)
        if self._store is not None:
            self._store.create(candidate)
        if self._audit is not None:
            self._audit.append(
                AuditEvent(
                    event_type="backlog.candidate.created",
                    entity_type="backlog_candidate",
                    entity_id=candidate.candidate_id,
                    tenant_id="unknown",
                    organization_id="unknown",
                    workspace_id="unknown",
                    actor_id=request.actor_id,
                    correlation_id=request.correlation_id,
                    outcome="DRAFT",
                    occurred_at=datetime.now(UTC),
                    reason="Backend-created backlog candidate draft.",
                    metadata={
                        "recommendation_id": request.recommendation.recommendation_id,
                        "finding_id": request.finding.finding_id,
                        "approval_state": candidate.approval_state.value,
                    },
                )
            )
        return candidate
