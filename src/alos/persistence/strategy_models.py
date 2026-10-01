"""SQLAlchemy projections for the authoritative strategy schema."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Protocol, Self, cast

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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

type StrategyDomain = (
    Plan
    | Objective
    | Target
    | Observation
    | TargetRelationship
    | PlanningAssumption
    | CascadeRun
    | TargetRevision
)


class DataclassInstance(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]


class StrategyBase(DeclarativeBase):
    """Separate metadata keeps legacy SQLite fixtures independent of PostgreSQL schemas."""


def _rehydrate[DomainT: StrategyDomain](domain: type[DomainT], payload: dict[str, Any]) -> DomainT:
    from alos.domains.strategy.models import (
        CascadeStatus,
        LifecycleState,
        ObservationKind,
        Period,
        PlanType,
        RelationshipType,
        RuleType,
        ScopeRef,
        VerificationState,
    )

    converters = {"period": Period, "scope": ScopeRef}
    for key, converter in converters.items():
        if isinstance(payload.get(key), dict):
            payload[key] = converter(**payload[key])
    enums = {
        "plan_type": PlanType,
        "lifecycle_state": LifecycleState,
        "relationship_type": RelationshipType,
        "status": CascadeStatus,
        "verification_state": VerificationState,
        "kind": ObservationKind,
        "rule_type": RuleType,
    }
    for key, enum in enums.items():
        if key in payload:
            payload[key] = enum(payload[key])
    for key in (
        "source_refs",
        "evidence_refs",
        "rule_snapshot",
        "constraint_snapshot",
        "result_snapshot",
        "candidate_snapshot",
    ):
        if isinstance(payload.get(key), list):
            payload[key] = tuple(payload[key])
    for key in ("created_at", "updated_at", "observed_at", "verified_at", "recorded_at"):
        timestamp = payload.get(key)
        if isinstance(timestamp, str):
            payload[key] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if domain.__name__ in {"Observation", "PlanningAssumption"}:
        value = payload.get("value")
        if value is not None and not isinstance(value, bool):
            payload["value"] = Decimal(str(value))
    return cast(DomainT, domain(**payload))


class StrategyPayloadRecord:
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    @classmethod
    def from_domain(cls, value: StrategyDomain) -> Self:
        data = asdict(cast(DataclassInstance, value))
        payload = json.loads(json.dumps(data, default=str))
        workspace = data.get("owner_workspace_id") or data.get("owner_workspace_ref") or "company"
        keys = cls.primary_values(data)
        created_at = (
            data.get("created_at")
            or data.get("recorded_at")
            or data.get("updated_at")
            or data.get("observed_at")
            or datetime.now().astimezone()
        )
        record = cls()
        for key, item in keys.items():
            setattr(record, key, item)
        record.tenant_id = str(data.get("tenant_id", "unknown"))
        record.organization_id = str(data.get("organization_id", "unknown"))
        record.workspace_id = str(workspace)
        record.created_by = str(data.get("created_by") or data.get("actor_id", "unknown"))
        record.created_at = cast(datetime, created_at)
        record.updated_at = cast(datetime, data.get("updated_at") or created_at)
        record.correlation_id = str(data.get("correlation_id", "unknown"))
        record.payload = payload
        return record

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def to_domain[DomainT: StrategyDomain](self, domain: type[DomainT]) -> DomainT:
        return _rehydrate(domain, dict(self.payload))


class StrategyPlanRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "plans"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": data["plan_id"],
            "version": data["version"],
            "lifecycle_state": data["lifecycle_state"],
        }


class StrategyObjectiveRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "objectives"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    objective_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), index=True)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "objective_id": data["objective_id"],
            "version": data["version"],
            "plan_id": data["plan_id"],
        }


class StrategyTargetRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "targets"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    target_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "target_id": data["target_id"],
            "version": data["version"],
            "plan_id": data["plan_id"],
            "lifecycle_state": data["lifecycle_state"],
        }


class StrategyObservationRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "target_observations"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    target_version: Mapped[int] = mapped_column(Integer)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "observation_id": data["observation_id"],
            "target_id": data["target_id"],
            "target_version": data["target_version"],
        }


class StrategyRelationshipRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "target_relationships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "organization_id",
            "relationship_type",
            "parent_target_id",
            "parent_target_version",
            "child_target_id",
            "child_target_version",
            name="uq_strategy_target_relationship_exact_versions",
        ),
        {"schema": "strategy"},
    )
    relationship_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    relationship_type: Mapped[str] = mapped_column(String(32), index=True)
    parent_target_id: Mapped[str] = mapped_column(String(128), index=True)
    parent_target_version: Mapped[int] = mapped_column(Integer)
    child_target_id: Mapped[str] = mapped_column(String(128), index=True)
    child_target_version: Mapped[int] = mapped_column(Integer)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "relationship_id": data["relationship_id"],
            "relationship_type": data["relationship_type"],
            "parent_target_id": data["parent_target_id"],
            "parent_target_version": data["parent_target_version"],
            "child_target_id": data["child_target_id"],
            "child_target_version": data["child_target_version"],
        }


class StrategyAssumptionRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "planning_assumptions"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    assumption_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {"assumption_id": data["assumption_id"], "version": data["version"]}


class StrategyCascadeRunRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "cascade_runs"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    cascade_run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {"cascade_run_id": data["cascade_run_id"], "status": data["status"]}


class StrategyRevisionRecord(StrategyPayloadRecord, StrategyBase):
    __tablename__ = "target_revisions"
    __table_args__ = {"schema": "strategy"}  # noqa: RUF012
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(128), index=True)

    @classmethod
    def primary_values(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {"revision_id": data["revision_id"], "target_id": data["target_id"]}
