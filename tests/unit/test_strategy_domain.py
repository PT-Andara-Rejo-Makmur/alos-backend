from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alos.audit import InMemoryAuditRepository
from alos.domains.strategy import CascadeEngine, InMemoryStrategyRepository, StrategyService
from alos.domains.strategy.constraints import activation_blockers, evaluate_constraint
from alos.domains.strategy.models import (
    CascadeRule,
    CascadeStatus,
    Constraint,
    ConstraintOutcome,
    LifecycleState,
    Objective,
    Observation,
    ObservationKind,
    Period,
    Plan,
    PlanType,
    RelationshipType,
    RuleType,
    ScopeRef,
    Target,
    TargetRelationship,
    VerificationState,
)
from alos.identity import Principal
from alos.main import create_app
from alos.persistence.strategy_models import StrategyPlanRecord
from alos.security.errors import PlatformError


def principal(
    *,
    tenant: str = "tenant-1",
    organization: str = "org-1",
    workspace: str = "executive",
    roles: frozenset[str] = frozenset({"EXECUTIVE", "BUSINESS_REVIEWER"}),
    permissions: frozenset[str] = frozenset(
        {
            "strategy.read",
            "strategy.company.manage",
            "strategy.review",
            "strategy.approve",
            "strategy.activate",
        }
    ),
) -> Principal:
    return Principal(
        "actor-1",
        tenant,
        organization,
        workspace,
        permissions=permissions,
        roles=roles,
    )


def period() -> Period:
    return Period("ANNUAL", "2027-01-01", "2027-12-31")


def plan(*, evidence: tuple[str, ...] = ("evidence:board",)) -> Plan:
    return Plan(
        "plan-rkap",
        1,
        PlanType.OPERATING_PLAN,
        "RKAP 2027",
        "tenant-1",
        "org-1",
        "executive",
        "EXECUTIVE",
        period(),
        ScopeRef("COMPANY"),
        LifecycleState.DRAFT,
        "actor-1",
        "corr-1",
        evidence_refs=evidence,
    )


def target(identity: str, *, workspace: str = "executive", scope: str = "COMPANY") -> Target:
    return Target(
        identity,
        1,
        identity.upper(),
        identity,
        "plan-rkap",
        1,
        "objective-growth",
        1,
        "tenant-1",
        "org-1",
        workspace,
        "EXECUTIVE" if scope == "COMPANY" else "WORKSPACE_LEAD",
        "COUNT",
        "COUNT",
        period(),
        ScopeRef(scope, None if scope == "COMPANY" else workspace),
        LifecycleState.DRAFT,
        "actor-1",
        "corr-1",
    )


def verified_target_observation(value: Target, actor: Principal) -> Observation:
    now = datetime.now(UTC)
    return Observation(
        f"observation-{value.target_id}",
        value.target_id,
        value.version,
        ObservationKind.TARGET,
        Decimal("10"),
        value.unit,
        value.period,
        "evidence:approved-target",
        "MANUAL_EVIDENCED",
        now,
        VerificationState.VERIFIED,
        ("evidence:approved-target",),
        actor.actor_id,
        verified_at=now,
        tenant_id=value.tenant_id,
        organization_id=value.organization_id,
        owner_workspace_id=value.owner_workspace_id,
        correlation_id="corr-observation",
    )


@pytest.mark.parametrize(
    ("rule_type", "inputs", "output", "status"),
    [
        (RuleType.DIRECT, {"value": Decimal("10.20")}, "10.20", CascadeStatus.VALID),
        (
            RuleType.SPLIT_FIXED,
            {"parent": Decimal("3"), "child:a": Decimal("1"), "child:b": Decimal("2")},
            "3",
            CascadeStatus.VALID,
        ),
        (
            RuleType.SPLIT_PERCENT,
            {"parent": Decimal("10.00"), "ratio": Decimal("0.25")},
            "2.5000",
            CascadeStatus.VALID,
        ),
        (
            RuleType.SUM_ROLLUP,
            {"a": Decimal("1.1"), "b": Decimal("2.2")},
            "3.3",
            CascadeStatus.VALID,
        ),
        (
            RuleType.RATIO_MULTIPLY,
            {"input": Decimal("7"), "ratio": Decimal("0.3")},
            "2.1",
            CascadeStatus.VALID,
        ),
        (
            RuleType.RATIO_DIVIDE_CEIL,
            {"input": Decimal("7"), "ratio": Decimal("0.3")},
            "24",
            CascadeStatus.VALID,
        ),
    ],
)
def test_decimal_rule_engine(rule_type, inputs, output, status):
    trace = CascadeEngine().calculate(CascadeRule("rule-1", rule_type, "out", {}), inputs=inputs)
    assert trace.output == output
    assert trace.status is status


def test_ratio_boundaries_fail_closed():
    engine = CascadeEngine()
    for ratio in (Decimal("0"), Decimal("-0.1"), Decimal("1.1")):
        trace = engine.calculate(
            CascadeRule("rule", RuleType.RATIO_DIVIDE_CEIL, "out", {}),
            inputs={"input": Decimal("1"), "ratio": ratio},
        )
        assert trace.status is CascadeStatus.INVALID
        assert trace.output is None
    missing = engine.calculate(
        CascadeRule("rule", RuleType.RATIO_MULTIPLY, "out", {}),
        inputs={"input": Decimal("1"), "ratio": None},
    )
    assert missing.status is CascadeStatus.INCOMPLETE


def test_split_percent_requires_exact_full_allocation():
    engine = CascadeEngine()
    rule = CascadeRule(
        "rule",
        RuleType.SPLIT_PERCENT,
        "out",
        {"require_full_allocation": True},
    )
    invalid = engine.calculate(
        rule,
        inputs={
            "parent": Decimal("100"),
            "ratio": Decimal("0.6"),
            "share:a": Decimal("0.6"),
            "share:b": Decimal("0.3"),
        },
    )
    assert invalid.status is CascadeStatus.INVALID
    valid = engine.calculate(
        rule,
        inputs={
            "parent": Decimal("100"),
            "ratio": Decimal("0.6"),
            "share:a": Decimal("0.6"),
            "share:b": Decimal("0.4"),
        },
    )
    assert valid.status is CascadeStatus.VALID and valid.output == "60.0"


def test_constraint_unknown_and_fail_block_activation():
    unknown = evaluate_constraint(Constraint("inventory", "INVENTORY", True, Decimal(10), None))
    failed = evaluate_constraint(Constraint("capacity", "CAPACITY", True, Decimal(10), Decimal(9)))
    passed = evaluate_constraint(Constraint("budget", "BUDGET", True, Decimal(9), Decimal(10)))
    assert unknown.outcome is ConstraintOutcome.UNKNOWN
    assert failed.outcome is ConstraintOutcome.FAIL
    assert passed.outcome is ConstraintOutcome.PASS
    assert activation_blockers((unknown, failed, passed)) == (unknown, failed)


def test_collection_authority_requires_role_and_permission():
    assert StrategyService.authority_projection(principal())["authorized_actions"] == [
        "CREATE_COMPANY_PLAN"
    ]
    division = principal(
        workspace="sales",
        roles=frozenset({"WORKSPACE_LEAD"}),
        permissions=frozenset({"strategy.read", "strategy.division.manage"}),
    )
    assert StrategyService.authority_projection(division)["authorized_actions"] == [
        "CREATE_DIVISION_PLAN"
    ]
    member = principal(
        workspace="sales",
        roles=frozenset({"WORKSPACE_MEMBER"}),
        permissions=frozenset({"strategy.read"}),
    )
    assert StrategyService.authority_projection(member)["authorized_actions"] == []


def test_sql_payload_round_trip_preserves_typed_version_and_timestamps():
    original = plan()
    restored = StrategyPlanRecord.from_domain(original).to_domain(Plan)
    assert restored == original
    assert restored.created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_lifecycle_revision_audit_and_immutability():
    repository = InMemoryStrategyRepository()
    audit = InMemoryAuditRepository()
    service = StrategyService(repository, audit)
    actor = principal()
    await service.create_plan(actor, plan())
    updated_plan = await service.update_plan(actor, "plan-rkap", {"name": "RKAP 2027 Final"})
    assert updated_plan.name == "RKAP 2027 Final"
    objective = Objective(
        "objective-growth",
        1,
        "plan-rkap",
        1,
        "OBJ-01",
        "Growth",
        "tenant-1",
        "org-1",
        "executive",
        "actor-1",
        "corr-1",
    )
    await service.create_objective(actor, objective)
    original = await service.create_target(actor, target("target-company"))
    await service.create_observation(actor, verified_target_observation(original, actor))
    submitted = await service.submit_plan(actor, "plan-rkap")
    assert submitted.lifecycle_state is LifecycleState.UNDER_REVIEW
    with pytest.raises(PlatformError, match="Only DRAFT"):
        await service.create_target(actor, target("late-target"))
    approved = await service.approve_plan(actor, "plan-rkap")
    with pytest.raises(PlatformError, match="Only DRAFT"):
        await service.update_plan(actor, "plan-rkap", {"name": "Must not change"})
    active = await service.activate_plan(
        actor,
        "plan-rkap",
        (Constraint("budget", "BUDGET", True, Decimal(1), Decimal(1)),),
    )
    assert approved.lifecycle_state is LifecycleState.APPROVED
    assert active.lifecycle_state is LifecycleState.ACTIVE
    active_target = await repository.get_target(original.target_id, 1)
    assert active_target is not None and active_target.lifecycle_state is LifecycleState.ACTIVE
    revision, draft = await service.revise_target(
        actor, original.target_id, "Updated basis", "corr-2"
    )
    assert revision.from_version == 1 and revision.to_version == 2
    assert draft.version == 2 and draft.lifecycle_state is LifecycleState.DRAFT
    historical = await repository.get_target(original.target_id, 1)
    assert historical is not None
    assert historical.version == 1 and historical.lifecycle_state is LifecycleState.ACTIVE
    events = audit.list_events(tenant_id="tenant-1")
    assert {event.event_type for event in events} >= {
        "PLAN_CREATED",
        "PLAN_UPDATED",
        "TARGET_CREATED",
        "PLAN_SUBMITTED",
        "PLAN_APPROVED",
        "PLAN_ACTIVATED",
        "TARGET_REVISION_CREATED",
    }


@pytest.mark.asyncio
async def test_scope_and_role_permission_denials():
    service = StrategyService(InMemoryStrategyRepository(), InMemoryAuditRepository())
    with pytest.raises(PlatformError) as cross_tenant:
        await service.create_plan(principal(tenant="other"), plan())
    assert cross_tenant.value.status_code == 403
    with pytest.raises(PlatformError) as missing_permission:
        await service.create_plan(principal(permissions=frozenset({"strategy.read"})), plan())
    assert missing_permission.value.status_code == 403
    division_actor = principal(
        workspace="sales",
        roles=frozenset({"WORKSPACE_LEAD"}),
        permissions=frozenset({"strategy.read", "strategy.division.manage"}),
    )
    division_plan = replace(
        plan(),
        plan_id="division-plan",
        owner_workspace_id="finance",
        scope=ScopeRef("DIVISION", "finance"),
    )
    with pytest.raises(PlatformError) as workspace_denial:
        await service.create_plan(division_actor, division_plan)
    assert workspace_denial.value.status_code == 403


@pytest.mark.asyncio
async def test_relationship_self_link_duplicate_and_cycle_denied():
    repository = InMemoryStrategyRepository()
    service = StrategyService(repository, InMemoryAuditRepository())
    actor = principal()
    await service.create_plan(actor, plan())
    for identity in ("a", "b", "c"):
        await service.create_target(actor, target(identity))
    with pytest.raises(PlatformError, match="cannot relate to itself"):
        await service.create_relationship(
            actor,
            TargetRelationship(
                "self",
                RelationshipType.CASCADE,
                "a",
                1,
                "a",
                1,
                "tenant-1",
                "org-1",
                "actor-1",
                "corr-1",
            ),
        )
    first = TargetRelationship(
        "a-b",
        RelationshipType.CASCADE,
        "a",
        1,
        "b",
        1,
        "tenant-1",
        "org-1",
        "actor-1",
        "corr-1",
    )
    await service.create_relationship(actor, first)
    await service.create_relationship(
        actor,
        TargetRelationship(
            "b-c",
            RelationshipType.CASCADE,
            "b",
            1,
            "c",
            1,
            "tenant-1",
            "org-1",
            "actor-1",
            "corr-1",
        ),
    )
    with pytest.raises(PlatformError, match="cycle"):
        await service.create_relationship(
            actor,
            TargetRelationship(
                "c-a",
                RelationshipType.CASCADE,
                "c",
                1,
                "a",
                1,
                "tenant-1",
                "org-1",
                "actor-1",
                "corr-1",
            ),
        )


@pytest.mark.asyncio
async def test_preview_is_immutable_and_accept_creates_draft_only():
    repository = InMemoryStrategyRepository()
    service = StrategyService(repository, InMemoryAuditRepository())
    actor = principal()
    await service.create_plan(actor, plan())
    root = await service.create_target(actor, target("root"))
    await service.create_observation(actor, verified_target_observation(root, actor))
    run = await service.preview_cascade(
        actor,
        root_target_id=root.target_id,
        root_target_version=1,
        rules=(CascadeRule("rule", RuleType.RATIO_DIVIDE_CEIL, "division", {}),),
        rule_inputs={"rule": {"input": Decimal("5"), "ratio": Decimal("0.5")}},
        constraints=(Constraint("capacity", "CAPACITY", True, Decimal(10), Decimal(10)),),
        correlation_id="corr-preview",
    )
    assert run.status is CascadeStatus.VALID
    assert await repository.get_target("division") is None
    with pytest.raises(PlatformError) as incomplete_accept:
        await service.accept_cascade(actor, run.cascade_run_id, ())
    assert incomplete_accept.value.code == "STRATEGY_CASCADE_TARGET_INVALID"
    derived = target("division", workspace="sales", scope="DIVISION")
    accepted = await service.accept_cascade(actor, run.cascade_run_id, (derived,))
    assert accepted.status is CascadeStatus.ACCEPTED
    stored = await repository.get_target("division")
    assert stored is not None and stored.lifecycle_state is LifecycleState.DRAFT
    assert stored.cascade_run_id == run.cascade_run_id
    derived_observations = await repository.list_observations("division", 1)
    assert len(derived_observations) == 1
    assert derived_observations[0].value == Decimal("10")
    assert derived_observations[0].verification_state is VerificationState.VERIFIED
    await service.submit_plan(actor, "plan-rkap")
    await service.approve_plan(actor, "plan-rkap")
    await service.activate_plan(actor, "plan-rkap")
    division_actor = principal(
        workspace="sales",
        roles=frozenset({"WORKSPACE_MEMBER"}),
        permissions=frozenset({"strategy.read"}),
    )
    visible = await service.list_targets(division_actor)
    assigned = next(item for item in visible if item.target_id == "division")
    assert assigned.lifecycle_state is LifecycleState.ACTIVE


@pytest.mark.asyncio
async def test_critical_unknown_blocks_activation():
    service = StrategyService(InMemoryStrategyRepository(), InMemoryAuditRepository())
    actor = principal()
    await service.create_plan(actor, plan())
    await service.submit_plan(actor, "plan-rkap")
    await service.approve_plan(actor, "plan-rkap")
    with pytest.raises(PlatformError) as blocked:
        await service.activate_plan(
            actor,
            "plan-rkap",
            (Constraint("inventory", "INVENTORY", True, Decimal(1), None),),
        )
    assert blocked.value.code == "STRATEGY_CONSTRAINT_BLOCKED"


@pytest.mark.asyncio
async def test_manual_observation_is_evidenced_persistent_and_kind_safe():
    repository = InMemoryStrategyRepository()
    service = StrategyService(repository, InMemoryAuditRepository())
    actor = principal()
    await service.create_plan(actor, plan())
    await service.create_target(actor, target("target-observed"))
    observation = Observation(
        "observation-target",
        "target-observed",
        1,
        ObservationKind.TARGET,
        Decimal("10.25"),
        "COUNT",
        period(),
        "evidence:manual",
        "MANUAL_EVIDENCED",
        datetime.now(UTC),
        VerificationState.PENDING_VERIFICATION,
        ("evidence:manual",),
        actor.actor_id,
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        owner_workspace_id=actor.workspace_id,
        correlation_id="corr-observation",
    )
    await service.create_observation(actor, observation)
    assert await repository.list_observations("target-observed", 1) == (observation,)
    assert observation.kind is ObservationKind.TARGET
    assert observation.value == Decimal("10.25")

    with pytest.raises(PlatformError) as missing_evidence:
        await service.create_observation(
            actor,
            replace(observation, observation_id="observation-invalid", evidence_refs=()),
        )
    assert missing_evidence.value.code == "STRATEGY_EVIDENCE_REQUIRED"
    await service.submit_plan(actor, "plan-rkap")
    await service.approve_plan(actor, "plan-rkap")
    with pytest.raises(PlatformError) as unverified:
        await service.activate_plan(actor, "plan-rkap")
    assert unverified.value.code == "STRATEGY_OBSERVATION_BLOCKED"


def test_contract_first_strategy_routes_are_registered():
    paths = create_app().openapi()["paths"]
    expected = {
        "/api/v1/strategy/plans",
        "/api/v1/strategy/authority",
        "/api/v1/strategy/plans/{plan_id}",
        "/api/v1/strategy/objectives",
        "/api/v1/strategy/targets",
        "/api/v1/strategy/targets/{target_id}",
        "/api/v1/strategy/targets/{target_id}/relationships",
        "/api/v1/strategy/targets/{target_id}/observations",
        "/api/v1/strategy/assumptions",
        "/api/v1/strategy/cascade/preview",
        "/api/v1/strategy/cascade-runs/{cascade_run_id}",
        "/api/v1/strategy/cascade-runs/{cascade_run_id}/accept",
        "/api/v1/strategy/plans/{plan_id}/submit",
        "/api/v1/strategy/plans/{plan_id}/approve",
        "/api/v1/strategy/plans/{plan_id}/activate",
        "/api/v1/strategy/targets/{target_id}/revisions",
    }
    assert expected.issubset(paths)
