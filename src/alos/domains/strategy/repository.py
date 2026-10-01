"""Strategy persistence ports and PostgreSQL implementation."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.audit.models import AuditEvent
from alos.audit.repository import SqlAuditRepository
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
from alos.persistence.strategy_models import (
    StrategyAssumptionRecord,
    StrategyCascadeRunRecord,
    StrategyObjectiveRecord,
    StrategyObservationRecord,
    StrategyPayloadRecord,
    StrategyPlanRecord,
    StrategyRelationshipRecord,
    StrategyRevisionRecord,
    StrategyTargetRecord,
)
from alos.security.errors import PlatformError


class StrategyRepository(Protocol):
    def transaction(
        self, tenant_id: str, organization_id: str
    ) -> AbstractAsyncContextManager[None]: ...
    async def save_plan(self, plan: Plan) -> None: ...
    async def get_plan(self, plan_id: str, version: int | None = None) -> Plan | None: ...
    async def list_plans(self, tenant_id: str, organization_id: str) -> tuple[Plan, ...]: ...
    async def save_objective(self, objective: Objective) -> None: ...
    async def get_objective(
        self, objective_id: str, version: int | None = None
    ) -> Objective | None: ...
    async def list_objectives(self, plan_id: str) -> tuple[Objective, ...]: ...
    async def save_target(self, target: Target) -> None: ...
    async def save_observation(self, observation: Observation) -> None: ...
    async def list_observations(
        self, target_id: str, target_version: int
    ) -> tuple[Observation, ...]: ...
    async def get_target(self, target_id: str, version: int | None = None) -> Target | None: ...
    async def list_targets(self, tenant_id: str, organization_id: str) -> tuple[Target, ...]: ...
    async def save_relationship(self, relationship: TargetRelationship) -> None: ...
    async def list_relationships(
        self, tenant_id: str, organization_id: str
    ) -> tuple[TargetRelationship, ...]: ...
    async def save_assumption(self, assumption: PlanningAssumption) -> None: ...
    async def get_assumption(
        self, assumption_id: str, version: int | None = None
    ) -> PlanningAssumption | None: ...
    async def list_assumptions(
        self, tenant_id: str, organization_id: str
    ) -> tuple[PlanningAssumption, ...]: ...
    async def save_cascade_run(self, run: CascadeRun) -> None: ...
    async def get_cascade_run(self, run_id: str) -> CascadeRun | None: ...
    async def save_revision(self, revision: TargetRevision) -> None: ...
    async def list_revisions(self, target_id: str) -> tuple[TargetRevision, ...]: ...


class InMemoryStrategyRepository:
    def __init__(self) -> None:
        self.plans: dict[tuple[str, int], Plan] = {}
        self.objectives: dict[tuple[str, int], Objective] = {}
        self.targets: dict[tuple[str, int], Target] = {}
        self.observations: dict[str, Observation] = {}
        self.relationships: dict[str, TargetRelationship] = {}
        self.assumptions: dict[tuple[str, int], PlanningAssumption] = {}
        self.runs: dict[str, CascadeRun] = {}
        self.revisions: dict[str, TargetRevision] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, tenant_id: str, organization_id: str) -> AsyncIterator[None]:
        async with self._lock:
            names = (
                "plans",
                "objectives",
                "targets",
                "observations",
                "relationships",
                "assumptions",
                "runs",
                "revisions",
            )
            before = {name: copy.deepcopy(getattr(self, name)) for name in names}
            try:
                yield
            except BaseException:
                for name, records in before.items():
                    setattr(self, name, records)
                raise

    @staticmethod
    def _insert[K, V](records: dict[K, V], key: K, value: V) -> None:
        if key in records:
            raise PlatformError(
                "STRATEGY_HISTORY_CONFLICT", "Historical record already exists.", status_code=409
            )
        records[key] = value

    async def save_plan(self, plan: Plan) -> None:
        self.plans[(plan.plan_id, plan.version)] = plan

    async def get_plan(self, plan_id: str, version: int | None = None) -> Plan | None:
        matches = [p for (identity, _), p in self.plans.items() if identity == plan_id]
        if not matches:
            return None
        if version is None:
            return max(matches, key=lambda item: item.version)
        return self.plans.get((plan_id, version))

    async def list_plans(self, tenant_id: str, organization_id: str) -> tuple[Plan, ...]:
        return tuple(
            p
            for p in self.plans.values()
            if p.tenant_id == tenant_id and p.organization_id == organization_id
        )

    async def save_objective(self, objective: Objective) -> None:
        self.objectives[(objective.objective_id, objective.version)] = objective

    async def get_objective(
        self, objective_id: str, version: int | None = None
    ) -> Objective | None:
        matches = [
            item
            for item in self.objectives.values()
            if item.objective_id == objective_id and (version is None or item.version == version)
        ]
        return max(matches, key=lambda item: item.version) if matches else None

    async def list_objectives(self, plan_id: str) -> tuple[Objective, ...]:
        return tuple(o for o in self.objectives.values() if o.plan_id == plan_id)

    async def save_target(self, target: Target) -> None:
        self.targets[(target.target_id, target.version)] = target

    async def save_observation(self, observation: Observation) -> None:
        self._insert(self.observations, observation.observation_id, observation)

    async def list_observations(
        self, target_id: str, target_version: int
    ) -> tuple[Observation, ...]:
        return tuple(
            item
            for item in self.observations.values()
            if item.target_id == target_id and item.target_version == target_version
        )

    async def get_target(self, target_id: str, version: int | None = None) -> Target | None:
        matches = [t for (identity, _), t in self.targets.items() if identity == target_id]
        if not matches:
            return None
        if version is None:
            return max(matches, key=lambda item: item.version)
        return self.targets.get((target_id, version))

    async def list_targets(self, tenant_id: str, organization_id: str) -> tuple[Target, ...]:
        return tuple(
            t
            for t in self.targets.values()
            if t.tenant_id == tenant_id and t.organization_id == organization_id
        )

    async def save_relationship(self, relationship: TargetRelationship) -> None:
        self._insert(self.relationships, relationship.relationship_id, relationship)

    async def list_relationships(
        self, tenant_id: str, organization_id: str
    ) -> tuple[TargetRelationship, ...]:
        return tuple(
            r
            for r in self.relationships.values()
            if r.tenant_id == tenant_id and r.organization_id == organization_id
        )

    async def save_assumption(self, assumption: PlanningAssumption) -> None:
        self._insert(self.assumptions, (assumption.assumption_id, assumption.version), assumption)

    async def get_assumption(
        self, assumption_id: str, version: int | None = None
    ) -> PlanningAssumption | None:
        matches = [
            item
            for item in self.assumptions.values()
            if item.assumption_id == assumption_id and (version is None or item.version == version)
        ]
        return max(matches, key=lambda item: item.version) if matches else None

    async def list_assumptions(
        self, tenant_id: str, organization_id: str
    ) -> tuple[PlanningAssumption, ...]:
        return tuple(
            a
            for a in self.assumptions.values()
            if a.tenant_id == tenant_id and a.organization_id == organization_id
        )

    async def save_cascade_run(self, run: CascadeRun) -> None:
        self.runs[run.cascade_run_id] = run

    async def get_cascade_run(self, run_id: str) -> CascadeRun | None:
        return self.runs.get(run_id)

    async def save_revision(self, revision: TargetRevision) -> None:
        self._insert(self.revisions, revision.revision_id, revision)

    async def list_revisions(self, target_id: str) -> tuple[TargetRevision, ...]:
        return tuple(r for r in self.revisions.values() if r.target_id == target_id)


class SqlStrategyRepository:
    """PostgreSQL repository; structured columns are authoritative, payload preserves projection."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory
        self._current: ContextVar[AsyncSession | None] = ContextVar(
            "strategy_session", default=None
        )

    @property
    def current_session(self) -> AsyncSession | None:
        return self._current.get()

    @asynccontextmanager
    async def transaction(self, tenant_id: str, organization_id: str) -> AsyncIterator[None]:
        lock_id = int.from_bytes(
            hashlib.sha256(f"strategy:{tenant_id}:{organization_id}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
            )
            token = self._current.set(session)
            try:
                yield
            except IntegrityError as exc:
                raise PlatformError(
                    "STRATEGY_HISTORY_CONFLICT",
                    "Historical record already exists.",
                    status_code=409,
                ) from exc
            finally:
                self._current.reset(token)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if current := self._current.get():
            yield current
        else:
            async with self._sessions() as session:
                yield session

    async def _insert(self, record: StrategyPayloadRecord) -> None:
        try:
            async with self._session() as session:
                session.add(record)
                await session.flush()
                if self._current.get() is None:
                    await session.commit()
        except IntegrityError as exc:
            raise PlatformError(
                "STRATEGY_HISTORY_CONFLICT", "Historical record already exists.", status_code=409
            ) from exc

    async def _upsert(self, record: StrategyPayloadRecord) -> None:
        async with self._session() as session:
            await session.merge(record)
            await session.flush()
            if self._current.get() is None:
                await session.commit()

    async def save_plan(self, plan: Plan) -> None:
        await self._upsert(StrategyPlanRecord.from_domain(plan))

    async def get_plan(self, plan_id: str, version: int | None = None) -> Plan | None:
        query = select(StrategyPlanRecord).where(StrategyPlanRecord.plan_id == plan_id)
        if version is not None:
            query = query.where(StrategyPlanRecord.version == version)
        query = query.order_by(StrategyPlanRecord.version.desc()).limit(1)
        async with self._session() as session:
            record = (await session.scalars(query)).first()
            return None if record is None else record.to_domain(Plan)

    async def list_plans(self, tenant_id: str, organization_id: str) -> tuple[Plan, ...]:
        query = select(StrategyPlanRecord).where(
            StrategyPlanRecord.tenant_id == tenant_id,
            StrategyPlanRecord.organization_id == organization_id,
        )
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(Plan) for record in records)

    async def get_objective(
        self, objective_id: str, version: int | None = None
    ) -> Objective | None:
        query = select(StrategyObjectiveRecord).where(
            StrategyObjectiveRecord.objective_id == objective_id
        )
        if version is not None:
            query = query.where(StrategyObjectiveRecord.version == version)
        async with self._session() as session:
            record = (
                await session.scalars(
                    query.order_by(StrategyObjectiveRecord.version.desc()).limit(1)
                )
            ).first()
            return None if record is None else record.to_domain(Objective)

    async def save_objective(self, objective: Objective) -> None:
        await self._upsert(StrategyObjectiveRecord.from_domain(objective))

    async def list_objectives(self, plan_id: str) -> tuple[Objective, ...]:
        query = select(StrategyObjectiveRecord).where(StrategyObjectiveRecord.plan_id == plan_id)
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(Objective) for record in records)

    async def save_target(self, target: Target) -> None:
        await self._upsert(StrategyTargetRecord.from_domain(target))

    async def save_observation(self, observation: Observation) -> None:
        await self._insert(StrategyObservationRecord.from_domain(observation))

    async def list_observations(
        self, target_id: str, target_version: int
    ) -> tuple[Observation, ...]:
        query = select(StrategyObservationRecord).where(
            StrategyObservationRecord.target_id == target_id,
            StrategyObservationRecord.target_version == target_version,
        )
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(Observation) for record in records)

    async def get_target(self, target_id: str, version: int | None = None) -> Target | None:
        query = select(StrategyTargetRecord).where(StrategyTargetRecord.target_id == target_id)
        if version is not None:
            query = query.where(StrategyTargetRecord.version == version)
        query = query.order_by(StrategyTargetRecord.version.desc()).limit(1)
        async with self._session() as session:
            record = (await session.scalars(query)).first()
            return None if record is None else record.to_domain(Target)

    async def list_targets(self, tenant_id: str, organization_id: str) -> tuple[Target, ...]:
        query = select(StrategyTargetRecord).where(
            StrategyTargetRecord.tenant_id == tenant_id,
            StrategyTargetRecord.organization_id == organization_id,
        )
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(Target) for record in records)

    async def save_relationship(self, relationship: TargetRelationship) -> None:
        await self._insert(StrategyRelationshipRecord.from_domain(relationship))

    async def list_relationships(
        self, tenant_id: str, organization_id: str
    ) -> tuple[TargetRelationship, ...]:
        query = select(StrategyRelationshipRecord).where(
            StrategyRelationshipRecord.tenant_id == tenant_id,
            StrategyRelationshipRecord.organization_id == organization_id,
        )
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(TargetRelationship) for record in records)

    async def get_assumption(
        self, assumption_id: str, version: int | None = None
    ) -> PlanningAssumption | None:
        query = select(StrategyAssumptionRecord).where(
            StrategyAssumptionRecord.assumption_id == assumption_id
        )
        if version is not None:
            query = query.where(StrategyAssumptionRecord.version == version)
        async with self._session() as session:
            record = (
                await session.scalars(
                    query.order_by(StrategyAssumptionRecord.version.desc()).limit(1)
                )
            ).first()
            return None if record is None else record.to_domain(PlanningAssumption)

    async def save_assumption(self, assumption: PlanningAssumption) -> None:
        await self._insert(StrategyAssumptionRecord.from_domain(assumption))

    async def list_assumptions(
        self, tenant_id: str, organization_id: str
    ) -> tuple[PlanningAssumption, ...]:
        query = select(StrategyAssumptionRecord).where(
            StrategyAssumptionRecord.tenant_id == tenant_id,
            StrategyAssumptionRecord.organization_id == organization_id,
        )
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(PlanningAssumption) for record in records)

    async def save_cascade_run(self, run: CascadeRun) -> None:
        await self._upsert(StrategyCascadeRunRecord.from_domain(run))

    async def get_cascade_run(self, run_id: str) -> CascadeRun | None:
        query = select(StrategyCascadeRunRecord).where(
            StrategyCascadeRunRecord.cascade_run_id == run_id
        )
        async with self._session() as session:
            record = (await session.scalars(query)).first()
            return None if record is None else record.to_domain(CascadeRun)

    async def save_revision(self, revision: TargetRevision) -> None:
        await self._insert(StrategyRevisionRecord.from_domain(revision))

    async def list_revisions(self, target_id: str) -> tuple[TargetRevision, ...]:
        query = select(StrategyRevisionRecord).where(StrategyRevisionRecord.target_id == target_id)
        async with self._session() as session:
            records = (await session.scalars(query)).all()
            return tuple(record.to_domain(TargetRevision) for record in records)


class SqlStrategyAuditRepository(SqlAuditRepository):
    """Strategy writes and their canonical audit events share one PostgreSQL transaction."""

    def __init__(self, repository: SqlStrategyRepository) -> None:
        super().__init__(repository._sessions)
        self._strategy = repository

    async def append(self, event: AuditEvent) -> None:
        if session := self._strategy.current_session:
            await self.append_in_session(session, event)
        else:
            await super().append(event)
