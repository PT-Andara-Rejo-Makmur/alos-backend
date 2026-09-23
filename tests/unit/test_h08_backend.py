from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.governance.gates import (
    AssuranceEvaluator,
    AutomatedAssuranceReport,
    ExpectedBehavior,
    ObservedBehavior,
    TestCategory,
)
from alos.governance.materiality import Materiality
from alos.releases import InMemoryReleaseAuthority, ReleaseConflictError, ReleaseState
from alos.registry import RegistryConflictError, VersionedContractRegistry
from alos.reviews.decisions import AuthoritativeDecision, AuthorityLevel, DecisionOutcome
from alos.reviews.packages import ReviewPackageReference


def _passing_assurance() -> AutomatedAssuranceReport:
    evaluator = AssuranceEvaluator()
    positive = evaluator.evaluate(
        test_id="positive_tool_success",
        category=TestCategory.POSITIVE,
        expected=ExpectedBehavior(status="SUCCESS"),
        observed=ObservedBehavior(status="SUCCESS"),
    )
    negative = evaluator.evaluate(
        test_id="negative_scope_denied",
        category=TestCategory.NEGATIVE,
        expected=ExpectedBehavior(status="DENIED", error_code="SCOPE_DENIED"),
        observed=ObservedBehavior(status="DENIED", error_code="SCOPE_DENIED"),
    )
    return AutomatedAssuranceReport(
        checks=(positive, negative),
        required_categories=frozenset({TestCategory.POSITIVE, TestCategory.NEGATIVE}),
    )


def _package(release_id: str, review_id: str, version: str) -> ReviewPackageReference:
    return ReviewPackageReference(
        review_id=review_id,
        tenant_id="tenant_001",
        workspace_id="workspace_001",
        subject_id="capability_example",
        subject_version=version,
        contract_version="1.0.0",
        evidence_uri=f"urn:alos:evidence:{release_id}",
        recorded_at=datetime.now(UTC),
    )


def _decision(
    *,
    decision_id: str,
    review_id: str,
    authority: AuthorityLevel,
    actor_id: str,
) -> AuthoritativeDecision:
    return AuthoritativeDecision(
        decision_id=decision_id,
        review_id=review_id,
        authority=authority,
        outcome=DecisionOutcome.APPROVED,
        actor_id=actor_id,
        rationale="Authoritative backend judgment.",
        decided_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_h08_registry_tracks_exact_capability_version() -> None:
    registry = VersionedContractRegistry(
        subject_type="capability",
        schema_id="https://schemas.alos.dev/v1/capability/capability-definition.schema.json",
        id_field="capability_id",
        version_field="version",
        contracts=CanonicalContractCatalog(Path("C:/Alos/alos-contracts")),
        audit=InMemoryAuditRepository(),
    )

    v1 = await registry.register(
        {
            "tenant_id": "tenant_001",
            "organization_id": "org_001",
            "workspace_id": "workspace_001",
            "capability_id": "capability_example",
            "version": "1.0.0",
            "name": "example capability",
            "purpose": "test",
            "owner": "platform-team",
            "capability_type": "AGENT",
            "lifecycle_state": "DEFINED",
            "scope_refs": ["scope.sources.external_read"],
            "permission_refs": ["research.external.read"],
            "backing_tool_ids": ["tool_example"],
            "metadata": {"human_gate_required": True},
        },
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        actor_id="actor_maker_001",
        correlation_id="corr_capability_1",
    )
    v2 = await registry.register(
        {
            "tenant_id": "tenant_001",
            "organization_id": "org_001",
            "workspace_id": "workspace_001",
            "capability_id": "capability_example",
            "version": "2.0.0",
            "name": "example capability",
            "purpose": "test",
            "owner": "platform-team",
            "capability_type": "AGENT",
            "lifecycle_state": "DEFINED",
            "scope_refs": ["scope.sources.external_read"],
            "permission_refs": ["research.external.read"],
            "backing_tool_ids": ["tool_example"],
            "metadata": {"human_gate_required": True},
        },
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        actor_id="actor_maker_001",
        correlation_id="corr_capability_2",
    )

    assert v1.version == "1.0.0"
    assert v2.version == "2.0.0"
    assert registry.get(tenant_id="tenant_001", workspace_id="workspace_001", subject_id="capability_example", version="1.0.0").version == "1.0.0"
    assert registry.get(tenant_id="tenant_001", workspace_id="workspace_001", subject_id="capability_example", version="2.0.0").version == "2.0.0"


@pytest.mark.asyncio
async def test_h08_release_requires_exact_version_and_authority() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    correlation = "corr_release_001"

    await authority.create(
        release_id="release_h08_001",
        review_id="review_h08_001",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        subject_id="capability_example",
        subject_version="2.0.0",
        materiality=Materiality.MATERIAL,
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.mark_implemented(
        "release_h08_001",
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.record_automated_assurance(
        "release_h08_001",
        _passing_assurance(),
        actor_id="actor_checker_001",
        correlation_id=correlation,
    )
    await authority.record_ai_review_package(
        "release_h08_001",
        _package("release_h08_001", "review_h08_001", "2.0.0"),
        actor_id="genesis_ai_review",
        correlation_id=correlation,
    )
    await authority.submit_for_it(
        "release_h08_001",
        actor_id="actor_checker_001",
        correlation_id=correlation,
    )
    await authority.record_it_decision(
        "release_h08_001",
        _decision(
            decision_id="decision_it_001",
            review_id="review_h08_001",
            authority=AuthorityLevel.IT,
            actor_id="actor_it_001",
        ),
        correlation_id=correlation,
    )
    await authority.submit_for_director(
        "release_h08_001",
        actor_id="actor_it_001",
        correlation_id=correlation,
    )
    await authority.record_director_decision(
        "release_h08_001",
        _decision(
            decision_id="decision_director_001",
            review_id="review_h08_001",
            authority=AuthorityLevel.DIRECTOR,
            actor_id="actor_director_001",
        ),
        correlation_id=correlation,
    )
    await authority.release(
        "release_h08_001",
        actor_id="actor_release_001",
        correlation_id=correlation,
    )
    final = authority.get("release_h08_001")
    assert final.subject_version == "2.0.0"
    assert final.state is ReleaseState.RELEASED


@pytest.mark.asyncio
async def test_h08_maker_self_approval_is_rejected() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    correlation = "corr_release_self_approve"

    await authority.create(
        release_id="release_h08_self_001",
        review_id="review_h08_self_001",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        subject_id="capability_example",
        subject_version="2.0.0",
        materiality=Materiality.MATERIAL,
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.mark_implemented(
        "release_h08_self_001",
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.record_automated_assurance(
        "release_h08_self_001",
        _passing_assurance(),
        actor_id="actor_checker_001",
        correlation_id=correlation,
    )
    await authority.record_ai_review_package(
        "release_h08_self_001",
        _package("release_h08_self_001", "review_h08_self_001", "2.0.0"),
        actor_id="genesis_ai_review",
        correlation_id=correlation,
    )
    await authority.submit_for_it(
        "release_h08_self_001",
        actor_id="actor_checker_001",
        correlation_id=correlation,
    )

    with pytest.raises(ReleaseConflictError, match="self-approve|maker|approval"):
        await authority.record_it_decision(
            "release_h08_self_001",
            _decision(
                decision_id="decision_it_self_001",
                review_id="review_h08_self_001",
                authority=AuthorityLevel.IT,
                actor_id="actor_maker_001",
            ),
            correlation_id=correlation,
        )


@pytest.mark.asyncio
async def test_h08_failed_assurance_blocks_release() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    correlation = "corr_release_failed_eval"

    await authority.create(
        release_id="release_h08_bad_eval",
        review_id="review_h08_bad_eval",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        subject_id="capability_example",
        subject_version="3.0.0",
        materiality=Materiality.NON_MATERIAL,
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.mark_implemented(
        "release_h08_bad_eval",
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )

    evaluator = AssuranceEvaluator()
    check = evaluator.evaluate(
        test_id="negative_scope",
        category=TestCategory.NEGATIVE,
        expected=ExpectedBehavior(status="DENIED", error_code="SCOPE_DENIED"),
        observed=ObservedBehavior(status="SUCCESS"),
    )
    report = AutomatedAssuranceReport(
        checks=(check,),
        required_categories=frozenset({TestCategory.NEGATIVE}),
    )

    with pytest.raises(ReleaseConflictError, match="did not pass"):
        await authority.record_automated_assurance(
            "release_h08_bad_eval",
            report,
            actor_id="actor_checker_001",
            correlation_id=correlation,
        )


@pytest.mark.asyncio
async def test_h08_release_rollback_keeps_authoritative_version() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    corr_1 = "corr_release_v1"
    corr_2 = "corr_release_v2"

    async def set_up(release_id: str, review_id: str, version: str) -> None:
        await authority.create(
            release_id=release_id,
            review_id=review_id,
            tenant_id="tenant_001",
            organization_id="org_001",
            workspace_id="workspace_001",
            subject_id="capability_example",
            subject_version=version,
            materiality=Materiality.NON_MATERIAL,
            actor_id="actor_maker_001",
            correlation_id=corr_1 if version == "1.0.0" else corr_2,
        )
        await authority.mark_implemented(release_id, actor_id="actor_maker_001", correlation_id=corr_1 if version == "1.0.0" else corr_2)
        await authority.record_automated_assurance(release_id, _passing_assurance(), actor_id="actor_checker_001", correlation_id=corr_1 if version == "1.0.0" else corr_2)
        await authority.record_ai_review_package(release_id, _package(release_id, review_id, version), actor_id="genesis_ai_review", correlation_id=corr_1 if version == "1.0.0" else corr_2)
        await authority.submit_for_it(release_id, actor_id="actor_checker_001", correlation_id=corr_1 if version == "1.0.0" else corr_2)
        await authority.record_it_decision(release_id, _decision(decision_id=f"decision_it_{version.replace('.', '_')}", review_id=review_id, authority=AuthorityLevel.IT, actor_id="actor_it_001"), correlation_id=corr_1 if version == "1.0.0" else corr_2)
        await authority.release(release_id, actor_id="actor_release_001", correlation_id=corr_1 if version == "1.0.0" else corr_2)
        await authority.activate(release_id, actor_id="actor_release_001", correlation_id=corr_1 if version == "1.0.0" else corr_2)

    await set_up("release_v1", "review_v1", "1.0.0")
    await set_up("release_v2", "review_v2", "2.0.0")
    await authority.activate_kill_switch("release_v2", actor_id="actor_it_001", reason="containment", correlation_id="corr_kill")
    await authority.clear_kill_switch("release_v2", actor_id="actor_it_001", reason="recovery", correlation_id="corr_clear")
    await authority.rollback("release_v2", target_release_id="release_v1", actor_id="actor_it_001", reason="restore stable version", correlation_id="corr_rollback")

    assert authority.get("release_v1").state is ReleaseState.ACTIVE
    assert authority.get("release_v2").state is ReleaseState.ROLLED_BACK


@pytest.mark.asyncio
async def test_h08_production_backlog_promotion_remains_authoritative() -> None:
    from alos.backlog.service import BacklogCandidateRequest, BacklogCandidateService
    from alos.research.models import ResearchFinding, ResearchRecommendation, ResearchDomain

    service = BacklogCandidateService()
    finding = ResearchFinding(
        finding_id="finding_h08_001",
        kind="RESEARCH",
        domain=ResearchDomain.TECHNOLOGY,
        statement="Evidence-backed change is ready for governance.",
        evidence_refs=("evidence_001",),
        confidence=0.9,
    )
    recommendation = ResearchRecommendation(
        recommendation_id="rec_h08_001",
        finding_id=finding.finding_id,
        recommendation="Promote after review and approval.",
        impact="Reduce operational risk.",
        priority_suggestion="P1",
        owner_suggestion="platform-team",
        evidence_refs=("evidence_001",),
        domain=ResearchDomain.TECHNOLOGY,
        correlation_id="corr_h08",
    )

    candidate = service.create(
        BacklogCandidateRequest(
            recommendation=recommendation,
            finding=finding,
            actor_id="platform_owner",
            scope_ref="research.technology",
            correlation_id="corr_h08",
        )
    )
    service.promote_to_review(candidate.candidate_id, actor_id="reviewer_001")
    service.approve(candidate.candidate_id, actor_id="reviewer_002")
    service.promote_to_production(candidate.candidate_id, actor_id="release_owner", scope_ref="research.technology")

    assert candidate.approval_state.value == "DRAFT"
    assert service.get(candidate.candidate_id).approval_state.value == "PROMOTED"


@pytest.mark.asyncio
async def test_h08_role_and_permission_injection_is_rejected() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    correlation = "corr_release_role_injection"

    await authority.create(
        release_id="release_h08_role_injection",
        review_id="review_h08_role_injection",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        subject_id="capability_example",
        subject_version="2.0.0",
        materiality=Materiality.MATERIAL,
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.mark_implemented(
        "release_h08_role_injection",
        actor_id="actor_maker_001",
        correlation_id=correlation,
    )
    await authority.record_automated_assurance(
        "release_h08_role_injection",
        _passing_assurance(),
        actor_id="actor_checker_001",
        correlation_id=correlation,
    )
    await authority.record_ai_review_package(
        "release_h08_role_injection",
        _package("release_h08_role_injection", "review_h08_role_injection", "2.0.0"),
        actor_id="genesis_ai_review",
        correlation_id=correlation,
    )
    await authority.submit_for_it(
        "release_h08_role_injection",
        actor_id="actor_checker_001",
        correlation_id=correlation,
    )

    with pytest.raises(ReleaseConflictError, match="maker|self-approve|approval"):
        await authority.record_it_decision(
            "release_h08_role_injection",
            _decision(
                decision_id="decision_it_role_injection",
                review_id="review_h08_role_injection",
                authority=AuthorityLevel.IT,
                actor_id="actor_maker_001",
            ),
            correlation_id=correlation,
        )
