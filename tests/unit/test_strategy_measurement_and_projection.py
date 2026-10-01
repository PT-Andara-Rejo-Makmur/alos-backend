from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_strategy_domain import period, plan, principal, target, verified_target_observation

from alos.audit import InMemoryAuditRepository
from alos.domains.executive.service import ExecutiveProjectionService
from alos.domains.strategy import InMemoryStrategyRepository, StrategyService
from alos.domains.strategy.measurement import performance_state
from alos.domains.strategy.models import (
    CascadeRule,
    CascadeStatus,
    LifecycleState,
    ObservationKind,
    PlanningAssumption,
    RuleType,
    VerificationState,
)
from alos.domains.strategy.read_model import target_detail
from alos.security.errors import PlatformError


async def setup_strategy():
    repository = InMemoryStrategyRepository()
    actor = principal()
    service = StrategyService(repository, InMemoryAuditRepository())
    await service.create_plan(actor, plan())
    root = await service.create_target(actor, target("measured"))
    return service, repository, actor, root


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ObservationKind))
@pytest.mark.parametrize("state", list(LifecycleState))
async def test_explicit_observation_lifecycle_matrix(kind, state):
    service, repository, actor, root = await setup_strategy()
    await repository.save_target(replace(root, lifecycle_state=state))
    if state is LifecycleState.ACTIVE:
        await repository.save_plan(replace(plan(), lifecycle_state=LifecycleState.ACTIVE))
    observation = replace(
        verified_target_observation(root, actor),
        kind=kind,
        verification_state=VerificationState.PENDING_VERIFICATION,
        verified_at=None,
    )
    allowed = (
        state is LifecycleState.DRAFT
        and kind in {ObservationKind.TARGET, ObservationKind.FORECAST, ObservationKind.ASSUMPTION}
    ) or (
        state is LifecycleState.ACTIVE
        and kind in {ObservationKind.ACTUAL, ObservationKind.FORECAST}
    )
    if allowed:
        saved = await service.create_observation(actor, observation)
        assert saved.recorded_at is not None
        assert saved.value == observation.value
    else:
        with pytest.raises(PlatformError) as error:
            await service.create_observation(actor, observation)
        assert error.value.status_code == 409
        assert await repository.list_observations(root.target_id, 1) == ()


@pytest.mark.asyncio
async def test_verification_appends_history_and_cannot_claim_monitoring_authority():
    service, repository, actor, root = await setup_strategy()
    original = await service.create_observation(
        actor,
        replace(
            verified_target_observation(root, actor),
            verification_state=VerificationState.PENDING_VERIFICATION,
            verified_at=None,
        ),
    )
    verified = await service.verify_observation(
        actor,
        root.target_id,
        original.observation_id,
        VerificationState.VERIFIED,
        "Checked planning evidence",
        "corr-verify",
    )
    assert verified.supersedes_observation_id == original.observation_id
    assert original.verification_state is VerificationState.PENDING_VERIFICATION
    assert verified.value == original.value
    assert len(await repository.list_observations(root.target_id, 1)) == 2
    with pytest.raises(PlatformError):
        await service.verify_observation(
            actor,
            root.target_id,
            original.observation_id,
            VerificationState.VERIFIED,
            "Duplicate",
            "corr-duplicate",
        )
    await service.submit_plan(actor, plan().plan_id)
    await service.approve_plan(actor, plan().plan_id)
    await service.activate_plan(actor, plan().plan_id)
    actual = replace(
        verified_target_observation(root, actor),
        observation_id="actual",
        kind=ObservationKind.ACTUAL,
        verification_state=VerificationState.PENDING_VERIFICATION,
    )
    await service.create_observation(actor, actual)
    with pytest.raises(PlatformError) as missing_grant:
        await service.verify_observation(
            actor,
            root.target_id,
            "actual",
            VerificationState.VERIFIED,
            "Monitoring review",
            "corr-monitor",
        )
    assert missing_grant.value.code == "STRATEGY_PERMISSION_DENIED"
    verifier = replace(actor, permissions=actor.permissions | {"strategy.observation.verify"})
    await service.verify_observation(
        verifier,
        root.target_id,
        "actual",
        VerificationState.VERIFIED,
        "Monitoring evidence checked",
        "corr-monitor",
    )
    assert len(await repository.list_observations(root.target_id, 1)) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "foreign"},
        {"organization_id": "foreign"},
        {"owner_workspace_id": "foreign"},
        {"actor_id": "forged"},
        {"evidence_refs": ()},
        {"source_mode": "SOURCE_LINKED", "source_ref": "fake.source"},
        {"unit": "IDR"},
    ],
)
async def test_observation_rejects_forged_scope_source_actor_and_unit(change):
    service, repository, actor, root = await setup_strategy()
    with pytest.raises(PlatformError):
        await service.create_observation(
            actor, replace(verified_target_observation(root, actor), **change)
        )
    assert await repository.list_observations(root.target_id, 1) == ()


@pytest.mark.asyncio
async def test_duplicate_observation_cannot_overwrite_history():
    service, repository, actor, root = await setup_strategy()
    original = await service.create_observation(actor, verified_target_observation(root, actor))
    with pytest.raises(PlatformError):
        await service.create_observation(actor, replace(original, value=Decimal(999)))
    assert (await repository.list_observations(root.target_id, 1))[0].value == Decimal(10)


@pytest.mark.parametrize(
    ("measurement", "planned", "actual", "expected"),
    [
        ("HIGHER_IS_BETTER", "10", "10", "ACHIEVED"),
        ("HIGHER_IS_BETTER", "10", "9", "OFF_TRACK"),
        ("HIGHER_IS_BETTER", "0", "0", "ACHIEVED"),
        ("LOWER_IS_BETTER", "10", "9", "ACHIEVED"),
        ("LOWER_IS_BETTER", "10", "11", "OFF_TRACK"),
        ("LOWER_IS_BETTER", "0", "0", "ACHIEVED"),
        ("EXACT", "10", "11", "OFF_TRACK"),
        ("EXACT", "10", "10", "ACHIEVED"),
        ("RANGE", "10", "10", "NOT_EVALUATED"),
        ("PERCENTAGE", "10", "10", "NOT_EVALUATED"),
        ("RATIO", "0.5", "0.5", "NOT_EVALUATED"),
        ("MILESTONE", "1", "1", "NOT_EVALUATED"),
        ("CUMULATIVE", "10", "10", "NOT_EVALUATED"),
        ("BINARY", True, True, "ACHIEVED"),
        ("BINARY", True, False, "OFF_TRACK"),
        ("BINARY", "1", "1", "NOT_EVALUATED"),
    ],
)
def test_conservative_performance_all_measurement_types(measurement, planned, actual, expected):
    actor = principal()
    root = replace(
        target("performance"),
        lifecycle_state=LifecycleState.ACTIVE,
        measurement_type=measurement,
        unit="BOOLEAN" if isinstance(planned, bool) else "COUNT",
    )
    observed = verified_target_observation(root, actor)
    values = (
        replace(observed, value=planned if isinstance(planned, bool) else Decimal(planned)),
        replace(
            observed,
            observation_id="actual",
            kind=ObservationKind.ACTUAL,
            value=actual if isinstance(actual, bool) else Decimal(actual),
        ),
    )
    assert performance_state(root, values, as_of=datetime(2028, 1, 1, tzinfo=UTC)) == expected


def test_performance_never_uses_unknown_unverified_forecast_or_unfinished_period():
    actor = principal()
    root = replace(target("performance"), lifecycle_state=LifecycleState.ACTIVE)
    planned = verified_target_observation(root, actor)
    actual = replace(planned, observation_id="actual", kind=ObservationKind.ACTUAL)
    later = datetime(2028, 1, 1, tzinfo=UTC)
    for values in (
        (planned,),
        (planned, replace(actual, value=None)),
        (planned, replace(actual, verification_state=VerificationState.UNVERIFIED)),
        (planned, replace(actual, kind=ObservationKind.FORECAST)),
        (planned, replace(actual, value=Decimal("NaN"))),
    ):
        assert performance_state(root, values, as_of=later) == "NOT_EVALUATED"
    assert (
        performance_state(root, (planned, actual), as_of=datetime(2027, 1, 1, tzinfo=UTC))
        == "NOT_EVALUATED"
    )


@pytest.mark.asyncio
async def test_revision_retains_active_version_until_new_lifecycle_finishes():
    service, repository, actor, root = await setup_strategy()
    await service.create_observation(actor, verified_target_observation(root, actor))
    for transition in (service.submit_plan, service.approve_plan, service.activate_plan):
        await transition(actor, plan().plan_id)
    revision, draft = await service.revise_target(
        actor, root.target_id, "New planning basis", "corr-revision"
    )
    assert revision.to_version == 2
    assert (
        await service.get_target(actor, root.target_id, 1)
    ).lifecycle_state is LifecycleState.ACTIVE
    assert len(await repository.list_observations(root.target_id, 1)) == 1
    assert await repository.list_observations(root.target_id, 2) == ()
    monitoring_actor = replace(
        actor, permissions=actor.permissions | {"strategy.observation.verify"}
    )
    pending = await service.create_observation(
        actor,
        replace(
            verified_target_observation(root, actor),
            observation_id="active-actual",
            kind=ObservationKind.ACTUAL,
            verification_state=VerificationState.UNVERIFIED,
        ),
    )
    decision = await service.verify_observation(
        monitoring_actor,
        root.target_id,
        pending.observation_id,
        VerificationState.VERIFIED,
        "Reviewed source evidence",
        "corr-old-active",
        version=1,
    )
    assert decision.target_version == 1
    await service.create_observation(
        actor,
        replace(
            verified_target_observation(draft, actor),
            observation_id="revised-value",
            value=Decimal(20),
        ),
    )
    with pytest.raises(PlatformError):
        await service.transition_target(actor, root.target_id, "activate")
    for action in ("submit", "approve", "activate"):
        await service.transition_target(actor, root.target_id, action)
    assert (
        await service.get_target(actor, root.target_id, 1)
    ).lifecycle_state is LifecycleState.SUPERSEDED
    assert (
        await service.get_target(actor, root.target_id, 2)
    ).lifecycle_state is LifecycleState.ACTIVE
    assert (await repository.list_observations(root.target_id, 1))[0].value == Decimal(10)
    with pytest.raises(PlatformError):
        await service.verify_observation(
            monitoring_actor,
            root.target_id,
            pending.observation_id,
            VerificationState.VERIFIED,
            "Historical source",
            "corr-closed",
            version=1,
        )


@pytest.mark.asyncio
async def test_cascade_rejects_tampered_metadata_hash_and_duplicate_acceptance():
    service, repository, actor, root = await setup_strategy()
    await service.create_observation(actor, verified_target_observation(root, actor))
    derived = target("derived")
    run = await service.preview_cascade(
        actor,
        root_target_id=root.target_id,
        root_target_version=1,
        rules=(CascadeRule("rule", RuleType.DIRECT, derived.target_id, {}),),
        rule_inputs={"rule": {}},
        constraints=(),
        correlation_id="corr-preview",
        derived_targets=(derived,),
    )
    assert run.status is CascadeStatus.VALID
    with pytest.raises(PlatformError):
        await service.accept_cascade(
            actor, run.cascade_run_id, (replace(derived, name="Tampered business meaning"),)
        )
    with pytest.raises(PlatformError):
        await service.accept_cascade(
            actor, run.cascade_run_id, (derived,), result_hash="sha256:" + "0" * 64
        )
    assert await repository.get_target(derived.target_id) is None
    await service.accept_cascade(
        actor,
        run.cascade_run_id,
        (derived,),
        input_hash=run.input_hash,
        result_hash=run.result_hash,
    )
    with pytest.raises(PlatformError):
        await service.accept_cascade(actor, run.cascade_run_id, (derived,))
    assert len(await repository.list_observations(derived.target_id, 1)) == 1


@pytest.mark.asyncio
async def test_projection_empty_connected_error_and_unknown_timestamp():
    repository = InMemoryStrategyRepository()
    service = StrategyService(repository, InMemoryAuditRepository())
    projection = ExecutiveProjectionService(service)
    actor = principal()
    empty = await projection.overview(actor)
    assert empty["strategy"]["status"] == "CONNECTED_EMPTY"
    assert empty["last_updated_at"] is None
    assert empty["strategy_data"]["targets"] == []
    # A retained legacy assumption has no known authoritative timestamp.
    assumption = PlanningAssumption(
        "legacy",
        1,
        "Legacy evidence",
        "CUSTOM",
        Decimal(1),
        "COUNT",
        period(),
        plan().scope,
        "",
        "MANUAL_EVIDENCED",
        ("evidence:legacy",),
        VerificationState.UNVERIFIED,
        "EXECUTIVE",
        actor.workspace_id,
        actor.tenant_id,
        actor.organization_id,
        actor.actor_id,
        "corr-legacy",
    )
    await repository.save_assumption(assumption)
    connected = await projection.overview(actor)
    assert connected["strategy"]["status"] == "CONNECTED"
    assert connected["last_updated_at"] is None
    assert all(item["status"] == "UNAVAILABLE" for item in connected["domains"])
    assert connected["shared_work"]["authoritative"] is False

    class UnavailableRepository(InMemoryStrategyRepository):
        async def list_plans(self, tenant_id, organization_id):
            raise ConnectionError("Database unavailable")

    failed = await ExecutiveProjectionService(
        StrategyService(UnavailableRepository(), InMemoryAuditRepository())
    ).overview(actor)
    assert failed["strategy"]["status"] == "ERROR"
    assert failed["strategy_data"] is None and failed["last_updated_at"] is None


@pytest.mark.asyncio
async def test_projection_requires_executive_role_and_permission():
    projection = ExecutiveProjectionService(
        StrategyService(InMemoryStrategyRepository(), InMemoryAuditRepository())
    )
    for actor in (principal(roles=frozenset({"IT_ADMIN"})), principal(permissions=frozenset())):
        with pytest.raises(PlatformError):
            await projection.overview(actor)


@pytest.mark.asyncio
async def test_backend_selected_observations_do_not_fallback_to_older_verified_value():
    service, _repository, actor, root = await setup_strategy()
    await service.create_observation(actor, verified_target_observation(root, actor))
    await service.create_observation(
        actor,
        replace(
            verified_target_observation(root, actor),
            observation_id="newer",
            value=None,
            verification_state=VerificationState.PENDING_VERIFICATION,
        ),
    )
    detail = await target_detail(service, actor, root)
    assert detail["selected_observations"]["target"]["value"] is None
    assert detail["performance_state"] == "NOT_EVALUATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rule_type,inputs",
    [
        (RuleType.SUM_ROLLUP, {"unlinked-business-value": Decimal(999)}),
        (RuleType.SPLIT_FIXED, {"child:forged-target": Decimal(999)}),
        (RuleType.RATIO_MULTIPLY, {"ratio": Decimal("NaN")}),
    ],
)
async def test_cascade_rejects_unlinked_and_nonfinite_inputs(rule_type, inputs):
    service, repository, actor, root = await setup_strategy()
    await service.create_observation(actor, verified_target_observation(root, actor))
    with pytest.raises(PlatformError) as denied:
        await service.preview_cascade(
            actor,
            root_target_id=root.target_id,
            root_target_version=1,
            rules=(CascadeRule("rule", rule_type, "derived", {}),),
            rule_inputs={"rule": inputs},
            constraints=(),
            correlation_id="corr-injection",
        )
    assert denied.value.code == "STRATEGY_CASCADE_INPUT_CONFLICT"
    assert await repository.get_target("derived") is None


@pytest.mark.asyncio
async def test_rollup_uses_only_verified_declared_targets_and_rejects_stale_preview():
    service, repository, actor, root = await setup_strategy()
    await service.create_observation(actor, verified_target_observation(root, actor))
    other = await service.create_target(actor, target("rollup-other"))
    await service.create_observation(
        actor,
        replace(
            verified_target_observation(other, actor),
            observation_id="other-source",
            value=Decimal(20),
        ),
    )
    candidate = target("rollup-derived")
    run = await service.preview_cascade(
        actor,
        root_target_id=root.target_id,
        root_target_version=1,
        rules=(
            CascadeRule(
                "rollup",
                RuleType.SUM_ROLLUP,
                candidate.target_id,
                {},
                ((root.target_id, 1), (other.target_id, 1)),
            ),
        ),
        rule_inputs={"rollup": {}},
        constraints=(),
        correlation_id="corr-rollup",
        derived_targets=(candidate,),
    )
    assert run.status is CascadeStatus.VALID
    assert run.result_snapshot[0]["output"] == "30"
    await service.create_observation(
        actor,
        replace(
            verified_target_observation(other, actor),
            observation_id="new-source",
            value=Decimal(21),
        ),
    )
    with pytest.raises(PlatformError) as stale:
        await service.accept_cascade(actor, run.cascade_run_id, (candidate,))
    assert stale.value.code == "STRATEGY_CASCADE_STALE"
    assert await repository.get_target(candidate.target_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e10000", "1.1"])
async def test_assumptions_reject_nonfinite_and_out_of_range_values(value):
    service, repository, actor, root = await setup_strategy()
    assumption = PlanningAssumption(
        "assumption.invalid-number",
        1,
        "CONVERSION_RATIO",
        "Explicit ratio",
        Decimal(value),
        "RATIO",
        root.period,
        root.scope,
        "",
        "MANUAL_EVIDENCED",
        ("evidence:ratio",),
        VerificationState.UNVERIFIED,
        "EXECUTIVE",
        actor.workspace_id,
        actor.tenant_id,
        actor.organization_id,
        actor.actor_id,
        "corr-invalid-assumption",
    )
    with pytest.raises(PlatformError) as invalid:
        await service.create_assumption(actor, assumption)
    assert invalid.value.code == "STRATEGY_ASSUMPTION_VALUE_INVALID"
    assert await repository.list_assumptions(actor.tenant_id, actor.organization_id) == ()


@pytest.mark.asyncio
async def test_display_labels_do_not_change_period_or_scope_identity():
    service, repository, actor, root = await setup_strategy()
    labeled = replace(root, period=replace(root.period, label="Annual planning context"))
    await repository.save_target(labeled)
    recorded = await service.create_observation(actor, verified_target_observation(root, actor))
    assert recorded.period == labeled.period
    revised_plan = await service.create_plan(
        actor, replace(plan(), version=2, scope=replace(plan().scope, label="Corporate"))
    )
    assert revised_plan.scope == plan().scope
