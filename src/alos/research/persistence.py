"""Backend persistence for canonical internal research and advisory draft backlog."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.identity import Principal
from alos.persistence.models import (
    BacklogCandidateRecord,
    ResearchFindingRecord,
    ResearchRecommendationRecord,
)


async def persist_result(
    factory: async_sessionmaker[AsyncSession], principal: Principal, result: dict[str, Any]
) -> None:
    """Persist canonical research output as Backend-owned records and draft backlog only."""
    finding_domains = {str(item["finding_id"]): str(item["domain"]) for item in result["findings"]}
    async with factory() as session:
        for finding in result["findings"]:
            finding_id = str(finding["finding_id"])
            if await session.get(ResearchFindingRecord, finding_id) is None:
                session.add(
                    ResearchFindingRecord(
                        finding_id=finding_id,
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        workspace_id=principal.workspace_id,
                        actor_id=principal.actor_id,
                        correlation_id=str(result["correlation_id"]),
                        kind=str(finding.get("finding_type", "RESEARCH")),
                        domain=str(finding.get("domain", "TECHNOLOGY")),
                        statement=str(finding["statement"]),
                        evidence_refs=[
                            str(item["evidence_id"]) for item in finding.get("evidence_refs", [])
                        ],
                        confidence=float(finding["confidence"]),
                        source_ref=next(
                            (str(item["source_id"]) for item in finding.get("evidence_refs", [])),
                            None,
                        ),
                        retrieval_metadata={
                            "research_id": result["research_id"],
                            "run_id": result["run_id"],
                            "output_state": result["output_state"],
                        },
                        created_at=datetime.now(UTC),
                    )
                )
        for recommendation in result["recommendations"]:
            recommendation_id = str(recommendation["recommendation_id"])
            finding_ids = [str(item) for item in recommendation.get("finding_ids", [])]
            finding_id = finding_ids[0] if finding_ids else "finding.unbound"
            evidence_ids = [
                str(item["evidence_id"]) for item in recommendation.get("evidence_refs", [])
            ]
            if await session.get(ResearchRecommendationRecord, recommendation_id) is None:
                session.add(
                    ResearchRecommendationRecord(
                        recommendation_id=recommendation_id,
                        finding_id=finding_id,
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        workspace_id=principal.workspace_id,
                        actor_id=principal.actor_id,
                        correlation_id=str(result["correlation_id"]),
                        recommendation=str(recommendation["summary"]),
                        impact=str(recommendation["recommended_action"]),
                        priority_suggestion="REVIEW",
                        owner_suggestion=None,
                        evidence_refs=evidence_ids,
                        domain=finding_domains[finding_id],
                        created_at=datetime.now(UTC),
                    )
                )
            candidate_id = f"candidate.{recommendation_id}"
            if (
                recommendation.get("backlog_candidate") is True
                and await session.get(BacklogCandidateRecord, candidate_id) is None
            ):
                session.add(
                    BacklogCandidateRecord(
                        candidate_id=candidate_id,
                        finding_id=finding_id,
                        recommendation_id=recommendation_id,
                        impact=str(recommendation["recommended_action"]),
                        priority_suggestion="REVIEW",
                        owner_suggestion=None,
                        evidence_refs=evidence_ids,
                        approval_state="DRAFT",
                        actor_id=principal.actor_id,
                        scope_ref=next(iter(sorted(principal.scopes))),
                        correlation_id=str(result["correlation_id"]),
                        created_at=datetime.now(UTC),
                    )
                )
        await session.commit()
