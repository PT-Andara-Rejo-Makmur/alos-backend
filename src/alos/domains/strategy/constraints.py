"""Fail-closed feasibility constraints for planning activation."""

from alos.domains.strategy.models import Constraint, ConstraintOutcome, ConstraintResult


def evaluate_constraint(constraint: Constraint) -> ConstraintResult:
    if not constraint.applies:
        return ConstraintResult(
            constraint.constraint_id,
            ConstraintOutcome.NOT_APPLICABLE,
            constraint.critical,
            "Constraint does not apply.",
        )
    if constraint.required_value is None or constraint.available_value is None:
        return ConstraintResult(
            constraint.constraint_id,
            ConstraintOutcome.UNKNOWN,
            constraint.critical,
            "Required authoritative source is missing.",
        )
    if constraint.required_value <= constraint.available_value:
        return ConstraintResult(
            constraint.constraint_id,
            ConstraintOutcome.PASS,
            constraint.critical,
            "Constraint satisfied.",
        )
    return ConstraintResult(
        constraint.constraint_id,
        ConstraintOutcome.FAIL,
        constraint.critical,
        "Required value exceeds available value.",
    )


def activation_blockers(results: tuple[ConstraintResult, ...]) -> tuple[ConstraintResult, ...]:
    return tuple(
        result
        for result in results
        if result.critical and result.outcome in {ConstraintOutcome.FAIL, ConstraintOutcome.UNKNOWN}
    )
