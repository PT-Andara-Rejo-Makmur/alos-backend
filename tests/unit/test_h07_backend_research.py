from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alos.backlog.service import BacklogCandidateRequest, BacklogCandidateService
from alos.persistence.base import Base
from alos.research.models import (
    BacklogCandidateState,
    ResearchDomain,
    ResearchFinding,
    ResearchRecommendation,
)
from alos.research.service import ResearchFindingService


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        execution_options={
            "schema_translate_map": {
                "ai_runtime": "main",
                "core": "main",
                "audit": "main",
                "jobs": "main",
                "governance": "main",
                "research": "main",
                "evidence": "main",
            }
        },
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def test_research_findings_persist_and_deduplicate(session_factory) -> None:
    service = ResearchFindingService(session_factory=session_factory)
    finding = ResearchFinding(
        finding_id="finding_h07_001",
        kind="RESEARCH",
        domain=ResearchDomain.TECHNOLOGY,
        statement="Public evidence confirms the issue.",
        evidence_refs=("evidence-1", "evidence-2"),
        confidence=0.83,
        source_ref="source:public:technology",
        retrieval_metadata={"retrieval_id": "retrieval_001"},
    )

    stored = service.create(finding, actor_id="analyst_001", correlation_id="corr_h07_001")
    assert stored.finding_id == "finding_h07_001"
    assert service.get("finding_h07_001").statement == "Public evidence confirms the issue."

    with pytest.raises(ValueError, match="duplicate|Duplicate"):
        service.create(
            ResearchFinding(
                finding_id="finding_h07_001",
                kind="OPERATIONAL",
                domain=ResearchDomain.MANAGEMENT,
                statement="Duplicate finding",
                evidence_refs=("evidence-3",),
                confidence=0.6,
            ),
            actor_id="analyst_002",
            correlation_id="corr_h07_002",
        )


def test_backlog_candidates_can_progress_from_draft_to_review(session_factory) -> None:
    service = BacklogCandidateService(session_factory=session_factory)
    finding = ResearchFinding(
        finding_id="finding_h07_002",
        kind="RESEARCH",
        domain=ResearchDomain.TECHNOLOGY,
        statement="A backlog item is warranted.",
        evidence_refs=("evidence-1",),
        confidence=0.91,
    )
    recommendation = ResearchRecommendation(
        recommendation_id="rec_h07_002",
        finding_id="finding_h07_002",
        recommendation="Implement the backend-governed evidence flow.",
        impact="Improve resilience.",
        priority_suggestion="P1",
        owner_suggestion="platform-team",
        evidence_refs=("evidence-1",),
        domain=ResearchDomain.TECHNOLOGY,
        correlation_id="corr_h07_backlog",
    )

    candidate = service.create(
        BacklogCandidateRequest(
            recommendation=recommendation,
            finding=finding,
            actor_id="product_owner_001",
            scope_ref="scope.sources.external_read",
            correlation_id="corr_h07_backlog",
        )
    )

    updated = service.promote_to_review(candidate.candidate_id, actor_id="reviewer_001")
    assert updated.approval_state is BacklogCandidateState.REVIEW

    with pytest.raises(ValueError, match="review|REVIEW"):
        service.promote_to_review(candidate.candidate_id, actor_id="reviewer_002")
