"""JSON-safe projections for strategy API responses."""

from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Protocol, cast, overload

from alos.domains.strategy.models import (
    CascadeRun,
    Objective,
    Observation,
    Plan,
    PlanningAssumption,
    Target,
    TargetRelationship,
    TargetRevision,
)

type JsonScalar = str | int | float | bool | Decimal | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
type ProjectedDomain = (
    Plan
    | Objective
    | Observation
    | PlanningAssumption
    | Target
    | TargetRelationship
    | TargetRevision
    | CascadeRun
)


class DataclassInstance(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]


@overload
def project(value: ProjectedDomain) -> dict[str, JsonValue]: ...


@overload
def project(value: dict[str, object]) -> dict[str, JsonValue]: ...


@overload
def project(value: tuple[object, ...] | list[object]) -> list[JsonValue]: ...


@overload
def project(value: object) -> JsonValue: ...


def project(value: object) -> JsonValue:
    if isinstance(value, Plan):
        return {
            "plan_id": value.plan_id,
            "version": value.version,
            "plan_type": value.plan_type.value,
            "name": value.name,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "owner_workspace_id": value.owner_workspace_id,
            "owner_role_ref": value.owner_role_ref,
            "period": project(value.period),
            "scope": project(value.scope),
            "lifecycle_state": value.lifecycle_state.value,
            "created_by": value.created_by,
            "correlation_id": value.correlation_id,
            "created_at": project(value.created_at),
            "updated_at": project(value.updated_at),
            "strategic_plan_id": value.strategic_plan_id,
            "strategic_plan_version": value.strategic_plan_version,
            "description": value.description,
            "materiality": value.materiality,
            "source_refs": list(value.source_refs),
            "evidence_refs": list(value.evidence_refs),
        }
    if isinstance(value, Objective):
        return {
            "objective_id": value.objective_id,
            "version": value.version,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "workspace_id": value.owner_workspace_id,
            "plan_id": value.plan_id,
            "plan_version": value.plan_version,
            "code": value.code,
            "name": value.name,
            "description": value.description,
            "scope": project(value.scope),
            "owner_role_ref": value.owner_role_ref,
            "lifecycle_state": value.lifecycle_state.value,
        }
    if isinstance(value, TargetRelationship):
        return {
            "relationship_id": value.relationship_id,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "parent_target_id": value.parent_target_id,
            "parent_target_version": value.parent_target_version,
            "child_target_id": value.child_target_id,
            "child_target_version": value.child_target_version,
            "relationship_type": value.relationship_type.value,
        }
    if isinstance(value, PlanningAssumption):
        return {
            "assumption_id": value.assumption_id,
            "version": value.version,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "owner_workspace_id": value.owner_workspace_id,
            "category": value.category,
            "name": value.name,
            "description": value.description,
            "value": value.value,
            "unit": value.unit,
            "period": project(value.period),
            "scope": project(value.scope),
            "source_ref": value.source_ref,
            "source_mode": value.source_mode,
            "evidence_refs": list(value.evidence_refs),
            "verification_state": value.verification_state.value,
            "owner_role_ref": value.owner_role_ref,
            "lifecycle_state": value.lifecycle_state.value,
        }
    if isinstance(value, CascadeRun):
        return {
            "cascade_run_id": value.cascade_run_id,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "correlation_id": value.correlation_id,
            "root_target_ref": {
                "id": value.root_target_id,
                "version": value.root_target_version,
            },
            "input_snapshot": project(value.input_snapshot),
            "assumption_snapshot": project(value.assumption_snapshot),
            "rule_snapshot": project(value.rule_snapshot),
            "constraint_snapshot": project(value.constraint_snapshot),
            "result_snapshot": project(value.result_snapshot),
            "input_hash": value.input_hash,
            "result_hash": value.result_hash,
            "created_by": value.created_by,
            "created_at": project(value.created_at),
            "status": value.status.value,
        }
    if isinstance(value, Observation):
        result: dict[str, JsonValue] = {
            "observation_id": value.observation_id,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "target_id": value.target_id,
            "target_version": value.target_version,
            "kind": value.kind.value,
            "value": project(value.value),
            "unit": value.unit,
            "period": project(value.period),
            "source_mode": value.source_mode,
            "source_ref": value.source_ref,
            "actor_id": value.actor_id,
            "observed_at": project(value.observed_at),
            "verified_at": project(value.verified_at),
            "verification_state": value.verification_state.value,
            "evidence_refs": list(value.evidence_refs),
        }
        if value.verified_at is None:
            result.pop("verified_at")
        return result
    if isinstance(value, TargetRevision):
        return {
            "revision_id": value.revision_id,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "target_id": value.target_id,
            "from_version": value.from_version,
            "to_version": value.to_version,
            "state": value.state,
            "reason": value.reason,
            "created_by": value.created_by,
            "created_at": project(value.created_at),
            "evidence_refs": list(value.evidence_refs),
        }
    if isinstance(value, Target):
        data: dict[str, JsonValue] = {
            "target_id": value.target_id,
            "version": value.version,
            "tenant_id": value.tenant_id,
            "organization_id": value.organization_id,
            "owner_workspace_id": value.owner_workspace_id,
            "code": value.code,
            "name": value.name,
            "description": value.description,
            "plan_ref": {"id": value.plan_id, "version": value.plan_version},
            "objective_ref": (
                None
                if value.objective_id is None
                else {"id": value.objective_id, "version": value.objective_version}
            ),
            "metric_code": value.metric_code or value.code,
            "scope": project(value.scope),
            "period": project(value.period),
            "measurement_type": value.measurement_type,
            "unit": value.unit,
            "owner_role_ref": value.owner_role_ref,
            "materiality": value.materiality,
            "lifecycle_state": value.lifecycle_state.value,
            "evidence_refs": list(value.evidence_refs),
            "source_refs": list(value.source_refs),
            "cascade_run_id": value.cascade_run_id,
            "created_by": value.created_by,
            "created_at": project(value.created_at),
            "updated_at": project(value.updated_at),
        }
        if value.cascade_run_id is None:
            data.pop("cascade_run_id")
        return data
    if is_dataclass(value):
        fields = cast(dict[str, object], asdict(cast(DataclassInstance, value)))
        return project(fields)
    if isinstance(value, dict):
        return {key: project(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [project(item) for item in value]
    if isinstance(value, Enum):
        return cast(JsonScalar, value.value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported strategy projection type: {type(value).__name__}")


def project_domains(
    values: tuple[ProjectedDomain, ...] | list[ProjectedDomain],
) -> list[dict[str, JsonValue]]:
    """Project a homogeneous domain collection without losing its object shape."""
    return [project(value) for value in values]
