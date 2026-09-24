"""Transactional PostgreSQL release lifecycle authority."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.governance.gates import AutomatedAssuranceReport
from alos.governance.materiality import Materiality, requires_director
from alos.persistence.models import (
    AuditRecord,
    AuthoritativeDecisionRecord,
    ReleaseLifecycleEventRecord,
    ReleaseRecord,
    ReviewPackageRecord,
)
from alos.releases.service import (
    GovernedRelease,
    ReleaseConflictError,
    ReleaseNotFoundError,
    ReleaseState,
)
from alos.reviews.decisions import AuthoritativeDecision, AuthorityLevel, DecisionOutcome
from alos.reviews.packages import ReviewPackageReference


class PersistentReleaseAuthority:
    """Persist release state and audit each transition in one database transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        registry_governed: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._registry_governed = registry_governed

    async def create(
        self,
        *,
        release_id: str,
        review_id: str,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        subject_id: str,
        subject_version: str,
        materiality: Materiality,
        actor_id: str,
        correlation_id: str,
    ) -> GovernedRelease:
        now = datetime.now(UTC)
        row = ReleaseRecord(
            release_id=release_id,
            review_id=review_id,
            state=ReleaseState.DRAFT.value,
            decided_by=actor_id,
            decided_at=now,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
            state_version=1,
            tenant_id=tenant_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            subject_version=subject_version,
            materiality=materiality.value,
            correlation_id=correlation_id,
            kill_switch_active=False,
            ever_released=False,
        )
        try:
            async with self._session_factory.begin() as session:
                session.add(row)
                await session.flush()
                self._record(session, row, None, ReleaseState.DRAFT, actor_id, correlation_id)
        except IntegrityError as exc:
            raise ReleaseConflictError("release_id already exists") from exc
        return self._from_row(row)

    async def get(
        self,
        release_id: str,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> GovernedRelease:
        async with self._session_factory() as session:
            row = await session.get(ReleaseRecord, release_id)
            if row is None:
                raise ReleaseNotFoundError("release was not found")
            self._require_context(row, tenant_id, workspace_id)
            return self._from_row(row)

    async def mark_implemented(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        return await self._transition(
            release_id,
            expected={ReleaseState.DRAFT, ReleaseState.REVISION_REQUIRED},
            target=ReleaseState.IMPLEMENTED,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    async def record_automated_assurance(
        self,
        release_id: str,
        report: AutomatedAssuranceReport,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> GovernedRelease:
        if not report.passed:
            raise ReleaseConflictError("automated assurance did not pass expected behavior")

        def update(row: ReleaseRecord) -> None:
            if actor_id == row.created_by:
                raise ReleaseConflictError("maker cannot perform automated assurance")
            row.assurance_actor_id = actor_id

        return await self._transition(
            release_id,
            expected={ReleaseState.IMPLEMENTED},
            target=ReleaseState.AUTOMATED_ASSURANCE,
            actor_id=actor_id,
            correlation_id=correlation_id,
            update=update,
        )

    async def record_ai_review_package(
        self,
        release_id: str,
        package: ReviewPackageReference,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> GovernedRelease:
        async with self._session_factory.begin() as session:
            row = await self._locked(session, release_id)
            self._expect(row, {ReleaseState.AUTOMATED_ASSURANCE})
            if (
                package.review_id != row.review_id
                or package.tenant_id != row.tenant_id
                or package.workspace_id != row.workspace_id
                or package.subject_id != row.subject_id
                or package.subject_version != row.subject_version
            ):
                raise ReleaseConflictError(
                    "ReviewPackage authority context does not match release"
                )
            existing = await session.get(ReviewPackageRecord, package.review_id)
            if existing is None:
                session.add(
                    ReviewPackageRecord(
                        review_id=package.review_id,
                        tenant_id=package.tenant_id,
                        workspace_id=package.workspace_id,
                        subject_id=package.subject_id,
                        subject_version=package.subject_version,
                        contract_version=package.contract_version,
                        evidence_uri=package.evidence_uri,
                        recorded_at=package.recorded_at,
                    )
                )
            elif self._package_tuple(existing) != self._package_reference_tuple(package):
                raise ReleaseConflictError("immutable ReviewPackage already has different content")
            self._apply_transition(
                session,
                row,
                ReleaseState.AI_REVIEWED,
                actor_id,
                correlation_id,
                actor_kind="SYSTEM",
            )
        return self._from_row(row)

    async def submit_for_it(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        return await self._transition(
            release_id,
            expected={ReleaseState.AI_REVIEWED},
            target=ReleaseState.READY_FOR_IT,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    async def record_it_decision(
        self,
        release_id: str,
        decision: AuthoritativeDecision,
        *,
        correlation_id: str,
    ) -> GovernedRelease:
        if decision.authority is not AuthorityLevel.IT:
            raise ReleaseConflictError("IT gate requires an IT decision")
        return await self._record_decision(
            release_id,
            decision,
            expected=ReleaseState.READY_FOR_IT,
            approved=ReleaseState.IT_APPROVED,
            correlation_id=correlation_id,
        )

    async def submit_for_director(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        async with self._session_factory.begin() as session:
            row = await self._locked(session, release_id)
            if not requires_director(Materiality(row.materiality)):
                raise ReleaseConflictError(
                    "non-material release does not require Director decision"
                )
            self._expect(row, {ReleaseState.IT_APPROVED})
            self._apply_transition(
                session, row, ReleaseState.READY_FOR_DIRECTOR, actor_id, correlation_id
            )
        return self._from_row(row)

    async def record_director_decision(
        self,
        release_id: str,
        decision: AuthoritativeDecision,
        *,
        correlation_id: str,
    ) -> GovernedRelease:
        if decision.authority is not AuthorityLevel.DIRECTOR:
            raise ReleaseConflictError("Director gate requires a Director decision")
        return await self._record_decision(
            release_id,
            decision,
            expected=ReleaseState.READY_FOR_DIRECTOR,
            approved=ReleaseState.DIRECTOR_APPROVED,
            correlation_id=correlation_id,
        )

    async def release(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        async with self._session_factory.begin() as session:
            row = await self._locked(session, release_id)
            expected = (
                {ReleaseState.DIRECTOR_APPROVED}
                if requires_director(Materiality(row.materiality))
                else {ReleaseState.IT_APPROVED}
            )
            self._expect(row, expected)
            row.ever_released = True
            self._apply_transition(
                session, row, ReleaseState.RELEASED, actor_id, correlation_id
            )
        return self._from_row(row)

    async def activate(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        self._require_unified_lifecycle()
        try:
            async with self._session_factory.begin() as session:
                row = await self._locked(session, release_id)
                self._expect(row, {ReleaseState.RELEASED})
                active = (
                    await session.execute(
                        select(ReleaseRecord)
                        .where(
                            ReleaseRecord.tenant_id == row.tenant_id,
                            ReleaseRecord.workspace_id == row.workspace_id,
                            ReleaseRecord.subject_id == row.subject_id,
                            ReleaseRecord.state == ReleaseState.ACTIVE.value,
                            ReleaseRecord.release_id != row.release_id,
                        )
                        .with_for_update()
                    )
                ).scalars()
                for previous in active:
                    self._apply_transition(
                        session,
                        previous,
                        ReleaseState.SUSPENDED,
                        actor_id,
                        correlation_id,
                        reason="Superseded by a newly activated release",
                    )
                row.ever_released = True
                self._apply_transition(
                    session, row, ReleaseState.ACTIVE, actor_id, correlation_id
                )
        except IntegrityError as exc:
            raise ReleaseConflictError("another release is already active") from exc
        return self._from_row(row)

    async def suspend(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        self._require_unified_lifecycle()
        if not reason.strip():
            raise ReleaseConflictError("suspension reason is required")
        return await self._transition(
            release_id,
            expected={ReleaseState.ACTIVE},
            target=ReleaseState.SUSPENDED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            reason=reason,
        )

    async def activate_kill_switch(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        self._require_unified_lifecycle()
        if not reason.strip():
            raise ReleaseConflictError("kill switch reason is required")

        def update(row: ReleaseRecord) -> None:
            row.kill_switch_active = True

        return await self._transition(
            release_id,
            expected={ReleaseState.ACTIVE},
            target=ReleaseState.SUSPENDED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            reason=reason,
            event_type="release.kill_switch_activated",
            update=update,
        )

    async def clear_kill_switch(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        self._require_unified_lifecycle()
        if not reason.strip():
            raise ReleaseConflictError("kill switch clear reason is required")

        def update(row: ReleaseRecord) -> None:
            if not row.kill_switch_active:
                raise ReleaseConflictError("there is no active kill switch")
            row.kill_switch_active = False

        return await self._transition(
            release_id,
            expected={ReleaseState.SUSPENDED},
            target=ReleaseState.ACTIVE,
            actor_id=actor_id,
            correlation_id=correlation_id,
            reason=reason,
            event_type="release.kill_switch_cleared",
            update=update,
        )

    async def rollback(
        self,
        release_id: str,
        *,
        target_release_id: str,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        self._require_unified_lifecycle()
        if not reason.strip():
            raise ReleaseConflictError("rollback reason is required")
        async with self._session_factory.begin() as session:
            rows = (
                await session.execute(
                    select(ReleaseRecord)
                    .where(ReleaseRecord.release_id.in_({release_id, target_release_id}))
                    .order_by(ReleaseRecord.release_id)
                    .with_for_update()
                )
            ).scalars().all()
            mapped = {item.release_id: item for item in rows}
            if release_id not in mapped or target_release_id not in mapped:
                raise ReleaseNotFoundError("release was not found")
            current, target = mapped[release_id], mapped[target_release_id]
            if current.kill_switch_active:
                raise ReleaseConflictError("clear the active kill switch before rollback")
            self._expect(current, {ReleaseState.ACTIVE})
            if release_id == target_release_id or self._subject_key(current) != self._subject_key(
                target
            ):
                raise ReleaseConflictError(
                    "rollback target must be another release of the same subject"
                )
            if not target.ever_released:
                raise ReleaseConflictError("rollback target must have been released previously")
            current.rollback_target_release_id = target_release_id
            self._apply_transition(
                session,
                current,
                ReleaseState.ROLLED_BACK,
                actor_id,
                correlation_id,
                event_type="release.rolled_back",
                reason=reason,
            )
            await session.flush()
            target.kill_switch_active = False
            self._apply_transition(
                session,
                target,
                ReleaseState.ACTIVE,
                actor_id,
                correlation_id,
                event_type="release.rollback_target_activated",
                reason=reason,
            )
        return self._from_row(current)

    async def execution_allowed(self, release_id: str) -> bool:
        self._require_unified_lifecycle()
        release = await self.get(release_id)
        return release.state is ReleaseState.ACTIVE and not release.kill_switch_active

    def _require_unified_lifecycle(self) -> None:
        if self._registry_governed:
            raise ReleaseConflictError(
                "agent release lifecycle requires unified registry orchestration"
            )

    async def _record_decision(
        self,
        release_id: str,
        decision: AuthoritativeDecision,
        *,
        expected: ReleaseState,
        approved: ReleaseState,
        correlation_id: str,
    ) -> GovernedRelease:
        try:
            async with self._session_factory.begin() as session:
                row = await self._locked(session, release_id)
                self._expect(row, {expected})
                self._require_decision_context(row, decision)
                if decision.actor_id == row.created_by:
                    raise ReleaseConflictError("maker cannot self-approve a release decision")
                if decision.authority is AuthorityLevel.IT:
                    if decision.actor_id == row.assurance_actor_id:
                        raise ReleaseConflictError("checker cannot approve its own assurance")
                    row.it_decision_id = decision.decision_id
                    row.it_actor_id = decision.actor_id
                else:
                    if decision.actor_id in {row.assurance_actor_id, row.it_actor_id}:
                        raise ReleaseConflictError(
                            "director must be independent from checker and IT approver"
                        )
                    row.director_decision_id = decision.decision_id
                    row.director_actor_id = decision.actor_id
                session.add(
                    AuthoritativeDecisionRecord(
                        decision_id=decision.decision_id,
                        review_id=decision.review_id,
                        authority=decision.authority.value,
                        outcome=decision.outcome.value,
                        actor_id=decision.actor_id,
                        rationale=decision.rationale,
                        decided_at=decision.decided_at,
                    )
                )
                target = {
                    DecisionOutcome.APPROVED: approved,
                    DecisionOutcome.RETURNED: ReleaseState.RETURNED,
                    DecisionOutcome.REJECTED: ReleaseState.REJECTED,
                    DecisionOutcome.HOLD: ReleaseState.HOLD,
                }[decision.outcome]
                self._apply_transition(
                    session, row, target, decision.actor_id, correlation_id
                )
                await session.flush()
        except IntegrityError as exc:
            raise ReleaseConflictError("decision_id has already been used") from exc
        return self._from_row(row)

    async def _transition(
        self,
        release_id: str,
        *,
        expected: set[ReleaseState],
        target: ReleaseState,
        actor_id: str,
        correlation_id: str,
        reason: str = "Authoritative release lifecycle transition",
        event_type: str = "release.transitioned",
        update: object | None = None,
    ) -> GovernedRelease:
        async with self._session_factory.begin() as session:
            row = await self._locked(session, release_id)
            self._expect(row, expected)
            if callable(update):
                update(row)
            self._apply_transition(
                session,
                row,
                target,
                actor_id,
                correlation_id,
                reason=reason,
                event_type=event_type,
            )
        return self._from_row(row)

    async def _locked(self, session: AsyncSession, release_id: str) -> ReleaseRecord:
        row = (
            await session.execute(
                select(ReleaseRecord)
                .where(ReleaseRecord.release_id == release_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ReleaseNotFoundError("release was not found")
        return row

    @staticmethod
    def _expect(row: ReleaseRecord, expected: set[ReleaseState]) -> None:
        if ReleaseState(row.state) not in expected:
            allowed = ", ".join(sorted(item.value for item in expected))
            raise ReleaseConflictError(f"release state {row.state} is not one of: {allowed}")

    @staticmethod
    def _apply_transition(
        session: AsyncSession,
        row: ReleaseRecord,
        target: ReleaseState,
        actor_id: str,
        correlation_id: str,
        *,
        actor_kind: str = "HUMAN",
        event_type: str = "release.transitioned",
        reason: str = "Authoritative release lifecycle transition",
    ) -> None:
        previous = row.state
        now = datetime.now(UTC)
        row.state = target.value
        row.decided_by = actor_id
        row.decided_at = now
        row.updated_at = now
        row.correlation_id = correlation_id
        row.state_version += 1
        session.add(
            ReleaseLifecycleEventRecord(
                release_id=row.release_id,
                from_state=previous,
                to_state=target.value,
                actor_id=actor_id,
                correlation_id=correlation_id,
                reason=reason,
                occurred_at=now,
            )
        )
        session.add(
            AuditRecord(
                event_type=event_type,
                entity_type="release",
                entity_id=row.release_id,
                tenant_id=row.tenant_id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                actor_id=actor_id,
                actor_kind=actor_kind,
                correlation_id=correlation_id,
                outcome=target.value,
                reason=reason,
                event_metadata={"from": previous, "to": target.value},
                occurred_at=now,
            )
        )

    @classmethod
    def _record(
        cls,
        session: AsyncSession,
        row: ReleaseRecord,
        previous: ReleaseState | None,
        target: ReleaseState,
        actor_id: str,
        correlation_id: str,
    ) -> None:
        now = row.created_at
        session.add(
            ReleaseLifecycleEventRecord(
                release_id=row.release_id,
                from_state=previous.value if previous else None,
                to_state=target.value,
                actor_id=actor_id,
                correlation_id=correlation_id,
                reason="Authoritative release lifecycle transition",
                occurred_at=now,
            )
        )
        session.add(
            AuditRecord(
                event_type="release.created",
                entity_type="release",
                entity_id=row.release_id,
                tenant_id=row.tenant_id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                actor_id=actor_id,
                actor_kind="HUMAN",
                correlation_id=correlation_id,
                outcome=target.value,
                reason="Authoritative release lifecycle transition",
                event_metadata={},
                occurred_at=now,
            )
        )

    @staticmethod
    def _from_row(row: ReleaseRecord) -> GovernedRelease:
        return GovernedRelease(
            release_id=row.release_id,
            review_id=row.review_id,
            tenant_id=row.tenant_id,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            subject_id=row.subject_id,
            subject_version=row.subject_version,
            materiality=Materiality(row.materiality),
            state=ReleaseState(row.state),
            created_by=row.created_by,
            correlation_id=row.correlation_id,
            created_at=row.created_at,
            it_decision_id=row.it_decision_id,
            assurance_actor_id=row.assurance_actor_id,
            it_actor_id=row.it_actor_id,
            director_decision_id=row.director_decision_id,
            director_actor_id=row.director_actor_id,
            kill_switch_active=row.kill_switch_active,
            rollback_target_release_id=row.rollback_target_release_id,
            ever_released=row.ever_released,
        )

    @staticmethod
    def _require_context(
        row: ReleaseRecord, tenant_id: str | None, workspace_id: str | None
    ) -> None:
        if tenant_id is not None and row.tenant_id != tenant_id:
            raise ReleaseNotFoundError("release was not found")
        if workspace_id is not None and row.workspace_id != workspace_id:
            raise ReleaseNotFoundError("release was not found")

    @staticmethod
    def _require_decision_context(
        row: ReleaseRecord, decision: AuthoritativeDecision
    ) -> None:
        expected = {
            "review_id": row.review_id,
            "tenant_id": row.tenant_id,
            "workspace_id": row.workspace_id,
            "release_id": row.release_id,
            "subject_id": row.subject_id,
        }
        for name, value in expected.items():
            supplied = getattr(decision, name)
            if supplied is not None and supplied != value:
                raise ReleaseConflictError(
                    f"decision {name} does not match release authority context"
                )

    @staticmethod
    def _subject_key(row: ReleaseRecord) -> tuple[str, str, str]:
        return row.tenant_id, row.workspace_id, row.subject_id

    @staticmethod
    def _package_tuple(row: ReviewPackageRecord) -> tuple[object, ...]:
        return (
            row.review_id,
            row.tenant_id,
            row.workspace_id,
            row.subject_id,
            row.subject_version,
            row.contract_version,
            row.evidence_uri,
            row.recorded_at,
        )

    @staticmethod
    def _package_reference_tuple(package: ReviewPackageReference) -> tuple[object, ...]:
        return (
            package.review_id,
            package.tenant_id,
            package.workspace_id,
            package.subject_id,
            package.subject_version,
            package.contract_version,
            package.evidence_uri,
            package.recorded_at,
        )
