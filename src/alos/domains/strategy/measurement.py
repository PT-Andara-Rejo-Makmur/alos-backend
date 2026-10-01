"""Explicit observation lifecycle and conservative deterministic performance evaluation."""

from datetime import UTC, datetime
from decimal import Decimal

from alos.domains.strategy.models import (
    LifecycleState,
    Observation,
    ObservationKind,
    Target,
    VerificationState,
)

OBSERVATION_STATES = {
    ObservationKind.TARGET: frozenset({LifecycleState.DRAFT}),
    ObservationKind.ACTUAL: frozenset({LifecycleState.ACTIVE}),
    ObservationKind.FORECAST: frozenset({LifecycleState.DRAFT, LifecycleState.ACTIVE}),
    ObservationKind.ASSUMPTION: frozenset({LifecycleState.DRAFT}),
}


def selected_observations(
    observations: tuple[Observation, ...],
) -> dict[ObservationKind, Observation]:
    superseded = {item.supersedes_observation_id for item in observations}
    selected: dict[ObservationKind, Observation] = {}
    ordered = sorted(
        observations,
        key=lambda item: (
            item.observed_at
            if item.kind in {ObservationKind.ACTUAL, ObservationKind.FORECAST}
            else (item.recorded_at or item.observed_at),
            item.record_sequence or 0,
            item.observation_id,
        ),
    )
    for item in ordered:
        if item.observation_id not in superseded:
            selected[item.kind] = item
    return selected


def performance_state(
    target: Target, observations: tuple[Observation, ...], *, as_of: datetime | None = None
) -> str:
    """Compare verified period totals only; undefined business policies remain unevaluated."""
    values = selected_observations(observations)
    planned, actual = values.get(ObservationKind.TARGET), values.get(ObservationKind.ACTUAL)
    if (
        target.lifecycle_state is not LifecycleState.ACTIVE
        or (as_of or datetime.now(UTC)).date().isoformat() <= target.period.ends_at
        or planned is None
        or actual is None
        or any(
            item.verification_state is not VerificationState.VERIFIED
            or item.value is None
            or item.unit != target.unit
            or item.period != target.period
            for item in (planned, actual)
        )
    ):
        return "NOT_EVALUATED"
    expected, measured = planned.value, actual.value
    if target.measurement_type == "BINARY":
        if not isinstance(expected, bool) or not isinstance(measured, bool):
            return "NOT_EVALUATED"
        return "ACHIEVED" if measured == expected else "OFF_TRACK"
    if not isinstance(expected, Decimal) or not isinstance(measured, Decimal):
        return "NOT_EVALUATED"
    if not expected.is_finite() or not measured.is_finite():
        return "NOT_EVALUATED"
    if target.measurement_type == "HIGHER_IS_BETTER":
        achieved = measured >= expected
    elif target.measurement_type == "LOWER_IS_BETTER":
        achieved = measured <= expected
    elif target.measurement_type == "EXACT":
        achieved = measured == expected
    else:
        # RANGE lacks bounds; PERCENTAGE/RATIO lack direction; MILESTONE lacks a
        # completion rule; CUMULATIVE lacks an aggregation rule. Do not invent one.
        return "NOT_EVALUATED"
    return "ACHIEVED" if achieved else "OFF_TRACK"
