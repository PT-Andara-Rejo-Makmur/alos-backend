"""Backend-owned backlog candidate creation from an approved recommendation."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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

    def __init__(
        self,
        *,
        audit: AuditSink | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._audit = audit
        self._seen: set[str] = set()
        self._candidate_cache: dict[str, BacklogCandidate] = {}
        self._store = (
            SqlBacklogCandidateStore(session_factory) if session_factory is not None else None
        )

    def can_execute(self, candidate: BacklogCandidate) -> bool:
        return (
            candidate.approval_state is BacklogCandidateState.PROMOTED
            or candidate.approval_state is BacklogCandidateState.APPROVED
        )

    def promote_to_review(self, candidate_id: str, *, actor_id: str) -> BacklogCandidate:
        if not candidate_id:
            raise ValueError("missing candidate id")
        candidate = self.get(candidate_id)
        if candidate.approval_state is not BacklogCandidateState.DRAFT:
            raise ValueError("candidate is not in DRAFT state and cannot be reviewed")
        candidate = BacklogCandidate(
            **{
                **candidate.model_dump(),
                "approval_state": BacklogCandidateState.REVIEW,
                "reviewed_by": actor_id,
                "reason": "Review recommended by backend authority.",
            }
        )
        if self._store is not None:
            with self._store._session_factory() as session:
                row = session.get(BacklogCandidateRecord, candidate_id)
                if row is None:
                    raise ValueError("backlog candidate was not found")
                row.approval_state = candidate.approval_state.value
                row.reviewed_by = actor_id
                row.reviewed_at = datetime.now(UTC)
                session.commit()
        self._candidate_cache[candidate_id] = candidate
        self._audit_transition(candidate, actor_id, "backlog.candidate.reviewed", "REVIEW")
        return candidate

    def approve(
        self, candidate_id: str, *, actor_id: str, reason: str | None = None
    ) -> BacklogCandidate:
        candidate = self.get(candidate_id)
        if candidate.approval_state is not BacklogCandidateState.REVIEW:
            raise ValueError("candidate is not in REVIEW state and cannot be approved")
        if (
            candidate.actor_id == actor_id
            or (candidate.actor_id or "").startswith("agent_")
            or actor_id == "agent"
        ):
            raise ValueError("agent cannot self-approve backlog candidates")
        candidate = BacklogCandidate(
            **{
                **candidate.model_dump(),
                "approval_state": BacklogCandidateState.APPROVED,
                "approved_by": actor_id,
                "reason": reason or "Approved by backend governance.",
            }
        )
        self._candidate_cache[candidate_id] = candidate
        self._audit_transition(candidate, actor_id, "backlog.candidate.approved", "APPROVED")
        return candidate

    def reject(
        self, candidate_id: str, *, actor_id: str, reason: str | None = None
    ) -> BacklogCandidate:
        candidate = self.get(candidate_id)
        if candidate.approval_state not in {
            BacklogCandidateState.DRAFT,
            BacklogCandidateState.REVIEW,
        }:
            raise ValueError("candidate cannot be rejected from its current state")
        candidate = BacklogCandidate(
            **{
                **candidate.model_dump(),
                "approval_state": BacklogCandidateState.REJECTED,
                "rejected_by": actor_id,
                "reason": reason or "Rejected by backend governance.",
            }
        )
        self._candidate_cache[candidate_id] = candidate
        self._audit_transition(candidate, actor_id, "backlog.candidate.rejected", "REJECTED")
        return candidate

    def promote_to_production(
        self,
        candidate_id: str,
        *,
        actor_id: str,
        scope_ref: str | None = None,
        reason: str | None = None,
    ) -> BacklogCandidate:
        candidate = self.get(candidate_id)
        if candidate.approval_state is not BacklogCandidateState.APPROVED:
            raise ValueError("candidate must be approved before production promotion")
        if candidate.actor_id == actor_id or actor_id == "agent":
            raise ValueError("agent cannot self-promote to production backlog")
        if scope_ref is None:
            scope_ref = candidate.scope_ref
        if scope_ref not in {"scope.sources.external_read", "research.technology"}:
            raise ValueError("cross-scope candidate promotion is rejected")
        if candidate.scope_ref not in {None, scope_ref}:
            raise ValueError("cross-scope candidate promotion is rejected")
        candidate = BacklogCandidate(
            **{
                **candidate.model_dump(),
                "approval_state": BacklogCandidateState.PROMOTED,
                "reason": reason or "Promoted to production backlog by backend governance.",
            }
        )
        self._candidate_cache[candidate_id] = candidate
        self._audit_transition(candidate, actor_id, "backlog.candidate.promoted", "PROMOTED")
        return candidate

    def get(self, candidate_id: str) -> BacklogCandidate:
        if self._store is not None:
            return self._store.get(candidate_id)
        if candidate_id not in self._candidate_cache:
            raise ValueError("backlog candidate was not found")
        return self._candidate_cache[candidate_id]

    def create(self, request: BacklogCandidateRequest) -> BacklogCandidate:
        if not request.recommendation.recommendation_id:
            raise ValueError("missing recommendation")
        if not request.finding.finding_id:
            raise ValueError("missing finding")
        if not request.recommendation.evidence_refs:
            raise ValueError("missing evidence")
        if request.scope_ref not in {"scope.sources.external_read", "research.technology"}:
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
            title=request.finding.title or request.recommendation.recommendation[:80],
            summary=request.finding.summary or request.finding.statement,
            impact=request.recommendation.impact,
            priority_suggestion=request.priority_suggestion
            or request.recommendation.priority_suggestion,
            owner_suggestion=request.owner_suggestion or request.recommendation.owner_suggestion,
            evidence_refs=tuple(request.evidence_refs or request.recommendation.evidence_refs),
            approval_state=BacklogCandidateState.DRAFT,
            actor_id=request.actor_id,
            scope_ref=request.scope_ref,
            correlation_id=request.correlation_id,
            reason="DRAFT candidate created by backend governance.",
        )
        self._seen.add(candidate_id)
        self._candidate_cache[candidate_id] = candidate
        if self._store is not None:
            self._store.create(candidate)
        self._audit_transition(candidate, request.actor_id, "backlog.candidate.created", "DRAFT")
        return candidate

    def _audit_transition(
        self, candidate: BacklogCandidate, actor_id: str, event_type: str, outcome: str
    ) -> None:
        if self._audit is None:
            return
        event = AuditEvent(
            event_type=event_type,
            entity_type="backlog_candidate",
            entity_id=candidate.candidate_id,
            tenant_id="unknown",
            organization_id="unknown",
            workspace_id="unknown",
            actor_id=actor_id,
            correlation_id=candidate.correlation_id or "unknown",
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            reason=candidate.reason or "Authoritative backlog lifecycle transition.",
            metadata={
                "candidate_id": candidate.candidate_id,
                "finding_id": candidate.finding_id,
                "recommendation_id": candidate.recommendation_id,
                "approval_state": candidate.approval_state.value,
                "scope_ref": candidate.scope_ref,
            },
        )
        result = self._audit.append(event)
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                task = asyncio.create_task(result)
                task.add_done_callback(lambda completed: completed.exception())
