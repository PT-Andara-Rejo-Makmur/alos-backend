"""Authoritative strategy planning domain models owned by ALOS Backend."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class LifecycleState(StrEnum):
    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class PlanType(StrEnum):
    STRATEGIC_PLAN = "STRATEGIC_PLAN"
    OPERATING_PLAN = "OPERATING_PLAN"


class RelationshipType(StrEnum):
    CASCADE = "CASCADE"
    CONTRIBUTES_TO = "CONTRIBUTES_TO"
    DEPENDS_ON = "DEPENDS_ON"


class ObservationKind(StrEnum):
    TARGET = "TARGET"
    ACTUAL = "ACTUAL"
    FORECAST = "FORECAST"
    ASSUMPTION = "ASSUMPTION"


class VerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"


class RuleType(StrEnum):
    DIRECT = "DIRECT"
    SPLIT_FIXED = "SPLIT_FIXED"
    SPLIT_PERCENT = "SPLIT_PERCENT"
    SUM_ROLLUP = "SUM_ROLLUP"
    RATIO_MULTIPLY = "RATIO_MULTIPLY"
    RATIO_DIVIDE_CEIL = "RATIO_DIVIDE_CEIL"
    LIMIT_CHECK = "LIMIT_CHECK"


class ConstraintOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105 - domain outcome, not a credential
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CascadeStatus(StrEnum):
    PREVIEW = "PREVIEW"
    VALID = "VALID"
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"
    ACCEPTED = "ACCEPTED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class ScopeRef:
    type: str
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class Period:
    granularity: str
    starts_at: str
    ends_at: str


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    version: int
    plan_type: PlanType
    name: str
    tenant_id: str
    organization_id: str
    owner_workspace_id: str
    owner_role_ref: str
    period: Period
    scope: ScopeRef
    lifecycle_state: LifecycleState
    created_by: str
    correlation_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    strategic_plan_id: str | None = None
    strategic_plan_version: int | None = None
    description: str | None = None
    materiality: str = "NON_MATERIAL"
    source_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def transition(self, lifecycle_state: LifecycleState) -> Plan:
        return replace(self, lifecycle_state=lifecycle_state, updated_at=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Objective:
    objective_id: str
    version: int
    plan_id: str
    plan_version: int
    code: str
    name: str
    tenant_id: str
    organization_id: str
    owner_workspace_id: str
    created_by: str
    correlation_id: str
    description: str | None = None
    scope: ScopeRef = field(default_factory=lambda: ScopeRef("COMPANY"))
    owner_role_ref: str = "EXECUTIVE"
    lifecycle_state: LifecycleState = LifecycleState.DRAFT


@dataclass(frozen=True, slots=True)
class Target:
    target_id: str
    version: int
    code: str
    name: str
    plan_id: str
    plan_version: int
    objective_id: str | None
    objective_version: int | None
    tenant_id: str
    organization_id: str
    owner_workspace_id: str
    owner_role_ref: str
    measurement_type: str
    unit: str
    period: Period
    scope: ScopeRef
    lifecycle_state: LifecycleState
    created_by: str
    correlation_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    description: str | None = None
    metric_code: str | None = None
    materiality: str = "NON_MATERIAL"
    source_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    cascade_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    target_id: str
    target_version: int
    kind: ObservationKind
    value: Decimal | bool | None
    unit: str
    period: Period
    source_ref: str
    source_mode: str
    observed_at: datetime
    verification_state: VerificationState
    evidence_refs: tuple[str, ...]
    actor_id: str
    verified_at: datetime | None = None
    tenant_id: str = "unknown"
    organization_id: str = "unknown"
    owner_workspace_id: str = "unknown"
    correlation_id: str = "unknown"


@dataclass(frozen=True, slots=True)
class TargetRelationship:
    relationship_id: str
    relationship_type: RelationshipType
    parent_target_id: str
    parent_target_version: int
    child_target_id: str
    child_target_version: int
    tenant_id: str
    organization_id: str
    created_by: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class PlanningAssumption:
    assumption_id: str
    version: int
    name: str
    category: str
    value: Decimal | bool | None
    unit: str
    period: Period
    scope: ScopeRef
    source_ref: str
    source_mode: str
    evidence_refs: tuple[str, ...]
    verification_state: VerificationState
    owner_role_ref: str
    owner_workspace_id: str
    tenant_id: str
    organization_id: str
    created_by: str
    correlation_id: str
    description: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT


@dataclass(frozen=True, slots=True)
class CascadeRule:
    rule_id: str
    rule_type: RuleType
    output_target_id: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CalculationTrace:
    rule_id: str
    rule_type: RuleType
    output_target_id: str
    inputs: dict[str, str | list[str] | None]
    rounding_mode: str | None
    output: str | None
    status: CascadeStatus
    message: str | None = None


@dataclass(frozen=True, slots=True)
class Constraint:
    constraint_id: str
    constraint_type: str
    critical: bool
    required_value: Decimal | None
    available_value: Decimal | None
    applies: bool = True


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    constraint_id: str
    outcome: ConstraintOutcome
    critical: bool
    message: str


@dataclass(frozen=True, slots=True)
class CascadeRun:
    cascade_run_id: str
    correlation_id: str
    root_target_id: str
    root_target_version: int
    input_snapshot: dict[str, Any]
    assumption_snapshot: dict[str, Any]
    rule_snapshot: tuple[dict[str, Any], ...]
    constraint_snapshot: tuple[dict[str, Any], ...]
    result_snapshot: tuple[dict[str, Any], ...]
    input_hash: str
    result_hash: str
    created_by: str
    created_at: datetime
    status: CascadeStatus
    tenant_id: str
    organization_id: str
    owner_workspace_id: str


@dataclass(frozen=True, slots=True)
class TargetRevision:
    revision_id: str
    target_id: str
    from_version: int
    to_version: int
    reason: str
    state: str
    created_by: str
    created_at: datetime
    correlation_id: str
    tenant_id: str = "unknown"
    organization_id: str = "unknown"
    owner_workspace_id: str = "unknown"
    evidence_refs: tuple[str, ...] = ()
