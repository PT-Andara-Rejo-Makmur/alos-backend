"""Authoritative release lifecycle with human gates, kill switch, and rollback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from alos.audit import AuditEvent, AuditSink
from alos.governance.gates import AutomatedAssuranceReport
from alos.governance.materiality import Materiality, requires_director
from alos.reviews.decisions import (
    AuthoritativeDecision,
    AuthorityLevel,
    DecisionOutcome,
)
from alos.reviews.packages import ReviewPackageReference


class ReleaseState(StrEnum):
    DRAFT = "DRAFT"
    IMPLEMENTED = "IMPLEMENTED"
    AUTOMATED_ASSURANCE = "AUTOMATED_ASSURANCE"
    AI_REVIEWED = "AI_REVIEWED"
    READY_FOR_IT = "READY_FOR_IT"
    IT_APPROVED = "IT_APPROVED"
    READY_FOR_DIRECTOR = "READY_FOR_DIRECTOR"
    DIRECTOR_APPROVED = "DIRECTOR_APPROVED"
    RELEASED = "RELEASED"
    ACTIVE = "ACTIVE"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"
    SUSPENDED = "SUSPENDED"
    ROLLED_BACK = "ROLLED_BACK"


class ReleaseConflictError(ValueError):
    pass


class ReleaseNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class GovernedRelease:
    release_id: str
    review_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    subject_id: str
    subject_version: str
    materiality: Materiality
    state: ReleaseState
    created_by: str
    correlation_id: str
    created_at: datetime
    it_decision_id: str | None = None
    director_decision_id: str | None = None
    kill_switch_active: bool = False
    rollback_target_release_id: str | None = None
    ever_released: bool = False


class InMemoryReleaseAuthority:
    """Deterministic domain baseline; persistence is represented by Backend ORM tables."""

    def __init__(self, audit: AuditSink) -> None:
        self._audit = audit
        self._releases: dict[str, GovernedRelease] = {}
        self._active_by_subject: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

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
        async with self._lock:
            if release_id in self._releases:
                raise ReleaseConflictError("release_id already exists")
            release = GovernedRelease(
                release_id=release_id,
                review_id=review_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                subject_id=subject_id,
                subject_version=subject_version,
                materiality=materiality,
                state=ReleaseState.DRAFT,
                created_by=actor_id,
                correlation_id=correlation_id,
                created_at=datetime.now(UTC),
            )
            self._releases[release_id] = release
        await self._record(release, actor_id, "release.created", "DRAFT")
        return release

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
        return await self._transition(
            release_id,
            expected={ReleaseState.IMPLEMENTED},
            target=ReleaseState.AUTOMATED_ASSURANCE,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    async def record_ai_review_package(
        self,
        release_id: str,
        package: ReviewPackageReference,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> GovernedRelease:
        current = self.get(release_id)
        if package.review_id != current.review_id:
            raise ReleaseConflictError("ReviewPackage does not belong to this release")
        if (
            package.tenant_id != current.tenant_id
            or package.workspace_id != current.workspace_id
            or package.subject_id != current.subject_id
            or package.subject_version != current.subject_version
        ):
            raise ReleaseConflictError("ReviewPackage authority context does not match release")
        return await self._transition(
            release_id,
            expected={ReleaseState.AUTOMATED_ASSURANCE},
            target=ReleaseState.AI_REVIEWED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            actor_kind="SYSTEM",
        )

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
        self._require_decision_context(release_id, decision)
        release = self.get(release_id)
        if decision.actor_id == release.created_by:
            raise ReleaseConflictError("maker cannot self-approve a release decision")
        target = self._decision_target(decision.outcome, ReleaseState.IT_APPROVED)
        return await self._transition(
            release_id,
            expected={ReleaseState.READY_FOR_IT},
            target=target,
            actor_id=decision.actor_id,
            correlation_id=correlation_id,
            it_decision_id=decision.decision_id,
        )

    async def submit_for_director(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        current = self.get(release_id)
        if not requires_director(current.materiality):
            raise ReleaseConflictError("non-material release does not require Director decision")
        return await self._transition(
            release_id,
            expected={ReleaseState.IT_APPROVED},
            target=ReleaseState.READY_FOR_DIRECTOR,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    async def record_director_decision(
        self,
        release_id: str,
        decision: AuthoritativeDecision,
        *,
        correlation_id: str,
    ) -> GovernedRelease:
        if decision.authority is not AuthorityLevel.DIRECTOR:
            raise ReleaseConflictError("Director gate requires a Director decision")
        self._require_decision_context(release_id, decision)
        release = self.get(release_id)
        if decision.actor_id == release.created_by:
            raise ReleaseConflictError("maker cannot self-approve a release decision")
        target = self._decision_target(decision.outcome, ReleaseState.DIRECTOR_APPROVED)
        return await self._transition(
            release_id,
            expected={ReleaseState.READY_FOR_DIRECTOR},
            target=target,
            actor_id=decision.actor_id,
            correlation_id=correlation_id,
            director_decision_id=decision.decision_id,
        )

    async def release(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        current = self.get(release_id)
        expected = (
            {ReleaseState.DIRECTOR_APPROVED}
            if requires_director(current.materiality)
            else {ReleaseState.IT_APPROVED}
        )
        return await self._transition(
            release_id,
            expected=expected,
            target=ReleaseState.RELEASED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ever_released=True,
        )

    async def activate(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        release = await self._transition(
            release_id,
            expected={ReleaseState.RELEASED},
            target=ReleaseState.ACTIVE,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ever_released=True,
        )
        key = self._subject_key(release)
        async with self._lock:
            other_id = self._active_by_subject.get(key)
        if other_id is not None and other_id != release_id:
            await self._transition(
                other_id,
                expected={ReleaseState.ACTIVE},
                target=ReleaseState.SUSPENDED,
                actor_id=actor_id,
                correlation_id=correlation_id,
                reason="Superseded by a newly activated release",
            )
        async with self._lock:
            self._active_by_subject[key] = release_id
        return release

    async def activate_kill_switch(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        if not reason.strip():
            raise ReleaseConflictError("kill switch reason is required")
        release = await self._transition(
            release_id,
            expected={ReleaseState.ACTIVE},
            target=ReleaseState.SUSPENDED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            kill_switch_active=True,
            event_type="release.kill_switch_activated",
            reason=reason,
        )
        async with self._lock:
            self._active_by_subject.pop(self._subject_key(release), None)
        return release

    async def clear_kill_switch(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        current = self.get(release_id)
        if not current.kill_switch_active:
            raise ReleaseConflictError("there is no active kill switch")
        if not reason.strip():
            raise ReleaseConflictError("kill switch clear reason is required")
        release = await self._transition(
            release_id,
            expected={ReleaseState.SUSPENDED},
            target=ReleaseState.ACTIVE,
            actor_id=actor_id,
            correlation_id=correlation_id,
            kill_switch_active=False,
            event_type="release.kill_switch_cleared",
            reason=reason,
        )
        async with self._lock:
            self._active_by_subject[self._subject_key(release)] = release_id
        return release

    async def rollback(
        self,
        release_id: str,
        *,
        target_release_id: str,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        current = self.get(release_id)
        target = self.get(target_release_id)
        if not reason.strip():
            raise ReleaseConflictError("rollback reason is required")
        if current.kill_switch_active:
            raise ReleaseConflictError("clear the active kill switch before rollback")
        if current.state is not ReleaseState.ACTIVE:
            raise ReleaseConflictError("only an active release can be rolled back")
        wrong_target = target_release_id == release_id or (
            self._subject_key(current) != self._subject_key(target)
        )
        if wrong_target:
            raise ReleaseConflictError(
                "rollback target must be another release of the same subject"
            )
        if not target.ever_released:
            raise ReleaseConflictError("rollback target must have been released previously")
        rolled_back = await self._transition(
            release_id,
            expected={ReleaseState.ACTIVE},
            target=ReleaseState.ROLLED_BACK,
            actor_id=actor_id,
            correlation_id=correlation_id,
            rollback_target_release_id=target_release_id,
            event_type="release.rolled_back",
            reason=reason,
        )
        async with self._lock:
            self._releases[target_release_id] = replace(
                target,
                state=ReleaseState.ACTIVE,
                kill_switch_active=False,
                correlation_id=correlation_id,
            )
            self._active_by_subject[self._subject_key(target)] = target_release_id
        await self._record(
            self._releases[target_release_id],
            actor_id,
            "release.rollback_target_activated",
            "ACTIVE",
            reason=reason,
        )
        return rolled_back

    def get(self, release_id: str) -> GovernedRelease:
        release = self._releases.get(release_id)
        if release is None:
            raise ReleaseNotFoundError("release was not found")
        return release

    def execution_allowed(self, release_id: str) -> bool:
        release = self.get(release_id)
        return release.state is ReleaseState.ACTIVE and not release.kill_switch_active

    async def _transition(
        self,
        release_id: str,
        *,
        expected: set[ReleaseState],
        target: ReleaseState,
        actor_id: str,
        correlation_id: str,
        actor_kind: str = "HUMAN",
        event_type: str = "release.transitioned",
        reason: str = "Authoritative release lifecycle transition",
        it_decision_id: str | None = None,
        director_decision_id: str | None = None,
        kill_switch_active: bool | None = None,
        rollback_target_release_id: str | None = None,
        ever_released: bool | None = None,
    ) -> GovernedRelease:
        async with self._lock:
            current = self._require(release_id)
            if current.state not in expected:
                allowed = ", ".join(sorted(state.value for state in expected))
                raise ReleaseConflictError(
                    f"release state {current.state.value} is not one of: {allowed}"
                )
            updated = replace(
                current,
                state=target,
                correlation_id=correlation_id,
                it_decision_id=it_decision_id or current.it_decision_id,
                director_decision_id=(director_decision_id or current.director_decision_id),
                kill_switch_active=(
                    current.kill_switch_active if kill_switch_active is None else kill_switch_active
                ),
                rollback_target_release_id=(
                    rollback_target_release_id or current.rollback_target_release_id
                ),
                ever_released=(current.ever_released if ever_released is None else ever_released),
            )
            self._releases[release_id] = updated
        await self._record(
            updated,
            actor_id,
            event_type,
            target.value,
            actor_kind=actor_kind,
            reason=reason,
            metadata={"from": current.state.value, "to": target.value},
        )
        return updated

    def _require_decision_context(self, release_id: str, decision: AuthoritativeDecision) -> None:
        release = self.get(release_id)
        if decision.review_id != release.review_id:
            raise ReleaseConflictError("decision review_id does not match release")
        expected = {
            "tenant_id": release.tenant_id,
            "workspace_id": release.workspace_id,
            "release_id": release.release_id,
            "subject_id": release.subject_id,
        }
        for field_name, expected_value in expected.items():
            supplied = getattr(decision, field_name)
            if supplied is not None and supplied != expected_value:
                raise ReleaseConflictError(
                    f"decision {field_name} does not match release authority context"
                )

    @staticmethod
    def _decision_target(outcome: DecisionOutcome, approved: ReleaseState) -> ReleaseState:
        return {
            DecisionOutcome.APPROVED: approved,
            DecisionOutcome.RETURNED: ReleaseState.RETURNED,
            DecisionOutcome.REJECTED: ReleaseState.REJECTED,
            DecisionOutcome.HOLD: ReleaseState.HOLD,
        }[outcome]

    def _require(self, release_id: str) -> GovernedRelease:
        release = self._releases.get(release_id)
        if release is None:
            raise ReleaseNotFoundError("release was not found")
        return release

    @staticmethod
    def _subject_key(release: GovernedRelease) -> tuple[str, str, str]:
        return release.tenant_id, release.workspace_id, release.subject_id

    async def _record(
        self,
        release: GovernedRelease,
        actor_id: str,
        event_type: str,
        outcome: str,
        *,
        actor_kind: str = "HUMAN",
        reason: str = "Authoritative release lifecycle transition",
        metadata: dict[str, object] | None = None,
    ) -> None:
        await self._audit.append(
            AuditEvent(
                event_type=event_type,
                entity_type="release",
                entity_id=release.release_id,
                tenant_id=release.tenant_id,
                organization_id=release.organization_id,
                workspace_id=release.workspace_id,
                actor_id=actor_id,
                actor_kind="SYSTEM" if actor_kind == "SYSTEM" else "HUMAN",
                correlation_id=release.correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                reason=reason,
                metadata=metadata or {},
            )
        )
