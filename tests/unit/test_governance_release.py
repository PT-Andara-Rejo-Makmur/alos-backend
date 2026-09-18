from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from alos.audit import InMemoryAuditRepository
from alos.governance.gates import (
    AssuranceEvaluator,
    AutomatedAssuranceReport,
    ExpectedBehavior,
    ObservedBehavior,
    TestCategory,
)
from alos.governance.materiality import Materiality
from alos.releases import (
    InMemoryReleaseAuthority,
    ReleaseConflictError,
    ReleaseState,
)
from alos.reviews.decisions import (
    AuthoritativeDecision,
    AuthorityLevel,
    DecisionOutcome,
)
from alos.reviews.packages import ReviewPackageReference


def passing_assurance() -> AutomatedAssuranceReport:
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


def package(release_id: str, review_id: str, version: str) -> ReviewPackageReference:
    return ReviewPackageReference(
        review_id=review_id,
        tenant_id="tenant_001",
        workspace_id="workspace_001",
        subject_id="agent_governed",
        subject_version=version,
        contract_version="1.0.0",
        evidence_uri=f"urn:alos:evidence:{release_id}",
        recorded_at=datetime.now(UTC),
    )


def decision(
    decision_id: str,
    review_id: str,
    authority: AuthorityLevel,
) -> AuthoritativeDecision:
    return AuthoritativeDecision(
        decision_id=decision_id,
        review_id=review_id,
        authority=authority,
        outcome=DecisionOutcome.APPROVED,
        actor_id=("actor_it_001" if authority is AuthorityLevel.IT else "actor_director_001"),
        rationale="Assurance evidence and authority constraints are acceptable.",
        decided_at=datetime.now(UTC),
    )


async def prepare_release(
    authority: InMemoryReleaseAuthority,
    *,
    release_id: str,
    review_id: str,
    version: str,
    materiality: Materiality,
) -> None:
    correlation_id = f"corr_{release_id}"
    await authority.create(
        release_id=release_id,
        review_id=review_id,
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        subject_id="agent_governed",
        subject_version=version,
        materiality=materiality,
        actor_id="actor_maker_001",
        correlation_id=correlation_id,
    )
    await authority.mark_implemented(
        release_id, actor_id="actor_maker_001", correlation_id=correlation_id
    )
    await authority.record_automated_assurance(
        release_id,
        passing_assurance(),
        actor_id="actor_checker_001",
        correlation_id=correlation_id,
    )
    await authority.record_ai_review_package(
        release_id,
        package(release_id, review_id, version),
        actor_id="genesis_ai_review",
        correlation_id=correlation_id,
    )
    await authority.submit_for_it(
        release_id, actor_id="actor_checker_001", correlation_id=correlation_id
    )
    await authority.record_it_decision(
        release_id,
        decision(f"decision_it_{version.replace('.', '_')}", review_id, AuthorityLevel.IT),
        correlation_id=correlation_id,
    )
    if materiality is Materiality.MATERIAL:
        await authority.submit_for_director(
            release_id, actor_id="actor_it_001", correlation_id=correlation_id
        )
        await authority.record_director_decision(
            release_id,
            decision(
                f"decision_director_{version.replace('.', '_')}",
                review_id,
                AuthorityLevel.DIRECTOR,
            ),
            correlation_id=correlation_id,
        )
    await authority.release(release_id, actor_id="actor_release_001", correlation_id=correlation_id)


def test_negative_test_is_evaluated_against_expected_behavior() -> None:
    check = AssuranceEvaluator().evaluate(
        test_id="negative_permission",
        category=TestCategory.NEGATIVE,
        expected=ExpectedBehavior(status="DENIED", error_code="PERMISSION_DENIED"),
        observed=ObservedBehavior(status="SUCCESS"),
    )

    assert check.passed is False
    assert "status expected DENIED" in check.mismatches[0]
    assert any("error_code" in mismatch for mismatch in check.mismatches)


def test_canonical_decision_shape_rejects_ai_as_final_authority() -> None:
    payload = {
        "decision_id": "decision_canonical_001",
        "review_id": "review_canonical_001",
        "decision_type": "IT",
        "outcome": "APPROVED",
        "decided_by": "actor_it_001",
        "rationale": "Automated assurance and review evidence are sufficient.",
        "decided_at": "2026-09-17T10:00:00Z",
    }
    parsed = AuthoritativeDecision.model_validate(payload)
    assert parsed.authority is AuthorityLevel.IT
    assert parsed.actor_id == "actor_it_001"

    payload["decision_type"] = "AI"
    with pytest.raises(ValidationError):
        AuthoritativeDecision.model_validate(payload)


@pytest.mark.asyncio
async def test_failed_negative_assurance_cannot_advance_release() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    await authority.create(
        release_id="release_failed_assurance",
        review_id="review_failed_assurance",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        subject_id="agent_governed",
        subject_version="0.9.0",
        materiality=Materiality.NON_MATERIAL,
        actor_id="actor_maker_001",
        correlation_id="corr_failed_assurance",
    )
    await authority.mark_implemented(
        "release_failed_assurance",
        actor_id="actor_maker_001",
        correlation_id="corr_failed_assurance",
    )
    failed_check = AssuranceEvaluator().evaluate(
        test_id="negative_scope",
        category=TestCategory.NEGATIVE,
        expected=ExpectedBehavior(status="DENIED", error_code="SCOPE_DENIED"),
        observed=ObservedBehavior(status="SUCCESS"),
    )
    report = AutomatedAssuranceReport(
        checks=(failed_check,),
        required_categories=frozenset({TestCategory.NEGATIVE}),
    )

    with pytest.raises(ReleaseConflictError, match="did not pass"):
        await authority.record_automated_assurance(
            "release_failed_assurance",
            report,
            actor_id="actor_checker_001",
            correlation_id="corr_failed_assurance",
        )


@pytest.mark.asyncio
async def test_material_release_requires_it_then_director_and_is_audited() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    await prepare_release(
        authority,
        release_id="release_material_001",
        review_id="review_material_001",
        version="1.0.0",
        materiality=Materiality.MATERIAL,
    )

    released = authority.get("release_material_001")
    assert released.state is ReleaseState.RELEASED
    assert released.it_decision_id == "decision_it_1_0_0"
    assert released.director_decision_id == "decision_director_1_0_0"
    outcomes = [event.outcome for event in audit.list_events(tenant_id="tenant_001")]
    assert "IT_APPROVED" in outcomes
    assert "DIRECTOR_APPROVED" in outcomes
    assert "RELEASED" in outcomes


@pytest.mark.asyncio
async def test_kill_switch_and_rollback_restore_previously_released_version() -> None:
    audit = InMemoryAuditRepository()
    authority = InMemoryReleaseAuthority(audit)
    await prepare_release(
        authority,
        release_id="release_v1",
        review_id="review_v1",
        version="1.0.0",
        materiality=Materiality.NON_MATERIAL,
    )
    await authority.activate("release_v1", actor_id="actor_release_001", correlation_id="corr_v1")
    await prepare_release(
        authority,
        release_id="release_v2",
        review_id="review_v2",
        version="2.0.0",
        materiality=Materiality.NON_MATERIAL,
    )
    await authority.activate("release_v2", actor_id="actor_release_001", correlation_id="corr_v2")
    suspended = await authority.activate_kill_switch(
        "release_v2",
        actor_id="actor_it_001",
        reason="Contain an observed production risk.",
        correlation_id="corr_kill_001",
    )
    assert suspended.state is ReleaseState.SUSPENDED
    assert authority.execution_allowed("release_v2") is False

    with pytest.raises(ReleaseConflictError, match="clear the active kill switch"):
        await authority.rollback(
            "release_v2",
            target_release_id="release_v1",
            actor_id="actor_it_001",
            reason="Rollback after containment.",
            correlation_id="corr_rollback_001",
        )

    await authority.clear_kill_switch(
        "release_v2",
        actor_id="actor_it_001",
        reason="Containment verified; proceed with controlled rollback.",
        correlation_id="corr_clear_001",
    )
    rolled_back = await authority.rollback(
        "release_v2",
        target_release_id="release_v1",
        actor_id="actor_it_001",
        reason="Restore the previously released stable version.",
        correlation_id="corr_rollback_001",
    )

    assert rolled_back.state is ReleaseState.ROLLED_BACK
    assert authority.get("release_v1").state is ReleaseState.ACTIVE
    assert authority.execution_allowed("release_v1") is True
    event_types = {event.event_type for event in audit.list_events(tenant_id="tenant_001")}
    assert {
        "release.kill_switch_activated",
        "release.kill_switch_cleared",
        "release.rolled_back",
        "release.rollback_target_activated",
    }.issubset(event_types)
