"""Atomic application service unifying agent release and registry lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.agents.registry import AgentRegistry
from alos.governance.materiality import Materiality
from alos.persistence.models import (
    AuditRecord,
    RegistryDefinitionRecord,
    ReleaseLifecycleEventRecord,
    ReleaseRecord,
)
from alos.persistence.registry import SqlRegistryStore
from alos.registry import RegistryEntry, RegistryState
from alos.releases.service import (
    GovernedRelease,
    ReleaseConflictError,
    ReleaseNotFoundError,
    ReleaseState,
)


class GovernedAgentLifecycle:
    """Change release and agent-registry state in one PostgreSQL transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: AgentRegistry,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    async def activate(
        self, release_id: str, *, actor_id: str, correlation_id: str
    ) -> GovernedRelease:
        changed: list[RegistryDefinitionRecord] = []
        try:
            async with self._session_factory.begin() as session:
                probe = await self._release_unlocked(session, release_id)
                releases = await self._subject_releases(session, probe)
                release = next(
                    item for item in releases if item.release_id == release_id
                )
                self._expect_release(release, {ReleaseState.RELEASED})
                registries = await self._subject_registries(session, release)
                target = self._matching_registry(registries, release)
                if RegistryState(target.lifecycle_state) not in {
                    RegistryState.DRAFT,
                    RegistryState.APPROVED,
                }:
                    raise ReleaseConflictError(
                        "registry version must be DRAFT or APPROVED before governed activation"
                    )
                self._validate_existing_active_pairs(releases, registries)
                for previous in releases:
                    if previous.release_id == release.release_id:
                        continue
                    if ReleaseState(previous.state) is ReleaseState.ACTIVE:
                        previous_registry = self._matching_registry(registries, previous)
                        self._transition_release(
                            session,
                            previous,
                            ReleaseState.SUSPENDED,
                            actor_id,
                            correlation_id,
                            reason="Superseded by a newly activated release",
                        )
                        self._transition_registry(
                            session,
                            previous_registry,
                            RegistryState.SUSPENDED,
                            actor_id,
                            correlation_id,
                            event_type="registry.version.suspended",
                            release_id=previous.release_id,
                        )
                        changed.append(previous_registry)
                if changed:
                    # Release partial unique indexes before activating the successor.
                    # The flush remains inside this transaction, so rollback is atomic.
                    await session.flush()
                release.ever_released = True
                self._transition_release(
                    session,
                    release,
                    ReleaseState.ACTIVE,
                    actor_id,
                    correlation_id,
                )
                self._transition_registry(
                    session,
                    target,
                    RegistryState.ACTIVE,
                    actor_id,
                    correlation_id,
                    event_type="registry.version.activated",
                    release_id=release.release_id,
                )
                changed.append(target)
                await session.flush()
        except IntegrityError as exc:
            raise ReleaseConflictError("another subject version is already active") from exc
        await self._publish(changed)
        return self._governed(release)

    async def suspend(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        return await self._suspend(
            release_id,
            actor_id=actor_id,
            reason=reason,
            correlation_id=correlation_id,
            kill=False,
        )

    async def activate_kill_switch(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        return await self._suspend(
            release_id,
            actor_id=actor_id,
            reason=reason,
            correlation_id=correlation_id,
            kill=True,
        )

    async def clear_kill_switch(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        if not reason.strip():
            raise ReleaseConflictError("kill switch clear reason is required")
        changed: list[RegistryDefinitionRecord] = []
        try:
            async with self._session_factory.begin() as session:
                release = await self._release(session, release_id)
                self._expect_release(release, {ReleaseState.SUSPENDED})
                if not release.kill_switch_active:
                    raise ReleaseConflictError("there is no active kill switch")
                releases = await self._subject_releases(session, release)
                registries = await self._subject_registries(session, release)
                if any(
                    item.release_id != release.release_id
                    and ReleaseState(item.state) is ReleaseState.ACTIVE
                    for item in releases
                ) or any(
                    item.version != release.subject_version
                    and RegistryState(item.lifecycle_state) is RegistryState.ACTIVE
                    for item in registries
                ):
                    raise ReleaseConflictError(
                        "cannot clear kill switch because an active successor exists"
                    )
                target = self._matching_registry(registries, release)
                if (
                    RegistryState(target.lifecycle_state) is not RegistryState.SUSPENDED
                    or target.release_id != release.release_id
                ):
                    raise ReleaseConflictError(
                        "suspended registry version does not match killed release"
                    )
                release.kill_switch_active = False
                self._transition_release(
                    session,
                    release,
                    ReleaseState.ACTIVE,
                    actor_id,
                    correlation_id,
                    event_type="release.kill_switch_cleared",
                    reason=reason,
                )
                self._transition_registry(
                    session,
                    target,
                    RegistryState.ACTIVE,
                    actor_id,
                    correlation_id,
                    event_type="registry.version.activated",
                    release_id=release.release_id,
                )
                changed.append(target)
                await session.flush()
        except IntegrityError as exc:
            raise ReleaseConflictError("another subject version is already active") from exc
        await self._publish(changed)
        return self._governed(release)

    async def rollback(
        self,
        release_id: str,
        *,
        target_release_id: str,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> GovernedRelease:
        if not reason.strip():
            raise ReleaseConflictError("rollback reason is required")
        if release_id == target_release_id:
            raise ReleaseConflictError("rollback target must be another release")
        changed: list[RegistryDefinitionRecord] = []
        try:
            async with self._session_factory.begin() as session:
                rows = (
                    await session.execute(
                        select(ReleaseRecord)
                        .where(ReleaseRecord.release_id.in_([release_id, target_release_id]))
                        .order_by(ReleaseRecord.release_id)
                        .with_for_update()
                    )
                ).scalars().all()
                mapped = {item.release_id: item for item in rows}
                if release_id not in mapped or target_release_id not in mapped:
                    raise ReleaseNotFoundError("release was not found")
                current, target_release = mapped[release_id], mapped[target_release_id]
                self._expect_release(current, {ReleaseState.ACTIVE})
                if current.kill_switch_active:
                    raise ReleaseConflictError("clear the active kill switch before rollback")
                if self._subject_key(current) != self._subject_key(target_release):
                    raise ReleaseConflictError(
                        "rollback target must be another release of the same subject"
                    )
                if not target_release.ever_released:
                    raise ReleaseConflictError("rollback target must have been released previously")
                registries = await self._subject_registries(session, current)
                current_registry = self._matching_registry(registries, current)
                target_registry = self._matching_registry(registries, target_release)
                if (
                    RegistryState(current_registry.lifecycle_state) is not RegistryState.ACTIVE
                    or current_registry.release_id != current.release_id
                ):
                    raise ReleaseConflictError(
                        "current release and registry are not jointly ACTIVE"
                    )
                if (
                    RegistryState(target_registry.lifecycle_state) is not RegistryState.SUSPENDED
                    or target_registry.release_id != target_release.release_id
                ):
                    raise ReleaseConflictError(
                        "rollback target registry is not a previously released suspended version"
                    )
                current.rollback_target_release_id = target_release_id
                self._transition_release(
                    session,
                    current,
                    ReleaseState.ROLLED_BACK,
                    actor_id,
                    correlation_id,
                    event_type="release.rolled_back",
                    reason=reason,
                )
                self._transition_registry(
                    session,
                    current_registry,
                    RegistryState.SUSPENDED,
                    actor_id,
                    correlation_id,
                    event_type="registry.version.suspended",
                    release_id=current.release_id,
                )
                # PostgreSQL may batch UPDATEs by primary key instead of assignment
                # order. Flush the deactivation first so partial ACTIVE indexes remain
                # satisfied while retaining one transaction for both sides.
                await session.flush()
                target_release.kill_switch_active = False
                self._transition_release(
                    session,
                    target_release,
                    ReleaseState.ACTIVE,
                    actor_id,
                    correlation_id,
                    event_type="release.rollback_target_activated",
                    reason=reason,
                )
                self._transition_registry(
                    session,
                    target_registry,
                    RegistryState.ACTIVE,
                    actor_id,
                    correlation_id,
                    event_type="registry.version.activated",
                    release_id=target_release.release_id,
                )
                changed.extend([current_registry, target_registry])
                await session.flush()
        except IntegrityError as exc:
            raise ReleaseConflictError("another subject version is already active") from exc
        await self._publish(changed)
        return self._governed(current)

    async def runtime_entry(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
    ) -> RegistryEntry:
        async with self._session_factory() as session:
            registry = (
                await session.execute(
                    select(RegistryDefinitionRecord).where(
                        RegistryDefinitionRecord.subject_type == "agent",
                        RegistryDefinitionRecord.tenant_id == tenant_id,
                        RegistryDefinitionRecord.organization_id == organization_id,
                        RegistryDefinitionRecord.workspace_id == workspace_id,
                        RegistryDefinitionRecord.subject_id == subject_id,
                        RegistryDefinitionRecord.version == version,
                    )
                )
            ).scalar_one_or_none()
            if registry is None or (
                RegistryState(registry.lifecycle_state) is not RegistryState.ACTIVE
            ):
                raise ReleaseConflictError("agent registry version is not ACTIVE")
            if registry.release_id is None:
                raise ReleaseConflictError("ACTIVE registry version has no governed release")
            release = await session.get(ReleaseRecord, registry.release_id)
            if (
                release is None
                or ReleaseState(release.state) is not ReleaseState.ACTIVE
                or release.kill_switch_active
                or not self._same_identity(release, registry)
            ):
                raise ReleaseConflictError("registry and release are not jointly ACTIVE")
            return SqlRegistryStore.entry_from_record(registry)

    async def _suspend(
        self,
        release_id: str,
        *,
        actor_id: str,
        reason: str,
        correlation_id: str,
        kill: bool,
    ) -> GovernedRelease:
        if not reason.strip():
            raise ReleaseConflictError(
                "kill switch reason is required" if kill else "suspension reason is required"
            )
        async with self._session_factory.begin() as session:
            release = await self._release(session, release_id)
            self._expect_release(release, {ReleaseState.ACTIVE})
            registry = await self._registry_for_release(session, release)
            if (
                RegistryState(registry.lifecycle_state) is not RegistryState.ACTIVE
                or registry.release_id != release.release_id
            ):
                raise ReleaseConflictError("release and registry are not jointly ACTIVE")
            release.kill_switch_active = kill
            self._transition_release(
                session,
                release,
                ReleaseState.SUSPENDED,
                actor_id,
                correlation_id,
                reason=reason,
                event_type=("release.kill_switch_activated" if kill else "release.transitioned"),
            )
            self._transition_registry(
                session,
                registry,
                RegistryState.SUSPENDED,
                actor_id,
                correlation_id,
                event_type="registry.version.suspended",
                release_id=release.release_id,
            )
            await session.flush()
        await self._publish([registry])
        return self._governed(release)

    async def _release(self, session: AsyncSession, release_id: str) -> ReleaseRecord:
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
    async def _release_unlocked(
        session: AsyncSession, release_id: str
    ) -> ReleaseRecord:
        row = await session.get(ReleaseRecord, release_id)
        if row is None:
            raise ReleaseNotFoundError("release was not found")
        return row

    async def _subject_releases(
        self, session: AsyncSession, release: ReleaseRecord
    ) -> list[ReleaseRecord]:
        return list(
            (
                await session.execute(
                    select(ReleaseRecord)
                    .where(
                        ReleaseRecord.tenant_id == release.tenant_id,
                        ReleaseRecord.organization_id == release.organization_id,
                        ReleaseRecord.workspace_id == release.workspace_id,
                        ReleaseRecord.subject_id == release.subject_id,
                    )
                    .order_by(ReleaseRecord.release_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
        )

    async def _subject_registries(
        self, session: AsyncSession, release: ReleaseRecord
    ) -> list[RegistryDefinitionRecord]:
        return list(
            (
                await session.execute(
                    select(RegistryDefinitionRecord)
                    .where(
                        RegistryDefinitionRecord.subject_type == "agent",
                        RegistryDefinitionRecord.tenant_id == release.tenant_id,
                        RegistryDefinitionRecord.organization_id == release.organization_id,
                        RegistryDefinitionRecord.workspace_id == release.workspace_id,
                        RegistryDefinitionRecord.subject_id == release.subject_id,
                    )
                    .order_by(RegistryDefinitionRecord.registry_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
        )

    async def _registry_for_release(
        self, session: AsyncSession, release: ReleaseRecord
    ) -> RegistryDefinitionRecord:
        registries = await self._subject_registries(session, release)
        return self._matching_registry(registries, release)

    @staticmethod
    def _matching_registry(
        registries: list[RegistryDefinitionRecord], release: ReleaseRecord
    ) -> RegistryDefinitionRecord:
        matches = [item for item in registries if item.version == release.subject_version]
        if len(matches) != 1:
            raise ReleaseConflictError(
                "release subject/version does not resolve to exactly one agent registry version"
            )
        registry = matches[0]
        if not GovernedAgentLifecycle._same_identity(release, registry):
            raise ReleaseConflictError("release and registry authority context do not match")
        return registry

    @staticmethod
    def _validate_existing_active_pairs(
        releases: list[ReleaseRecord], registries: list[RegistryDefinitionRecord]
    ) -> None:
        active_releases = {
            item.release_id: item
            for item in releases
            if ReleaseState(item.state) is ReleaseState.ACTIVE
        }
        active_registries = [
            item
            for item in registries
            if RegistryState(item.lifecycle_state) is RegistryState.ACTIVE
        ]
        if len(active_releases) != len(active_registries):
            raise ReleaseConflictError("existing release and registry ACTIVE state is inconsistent")
        for registry in active_registries:
            release = active_releases.get(registry.release_id or "")
            if release is None or not GovernedAgentLifecycle._same_identity(release, registry):
                raise ReleaseConflictError(
                    "existing release and registry ACTIVE identity is inconsistent"
                )

    @staticmethod
    def _same_identity(
        release: ReleaseRecord, registry: RegistryDefinitionRecord
    ) -> bool:
        return (
            registry.subject_type == "agent"
            and release.tenant_id == registry.tenant_id
            and release.organization_id == registry.organization_id
            and release.workspace_id == registry.workspace_id
            and release.subject_id == registry.subject_id
            and release.subject_version == registry.version
        )

    @staticmethod
    def _expect_release(row: ReleaseRecord, expected: set[ReleaseState]) -> None:
        if ReleaseState(row.state) not in expected:
            allowed = ", ".join(sorted(item.value for item in expected))
            raise ReleaseConflictError(f"release state {row.state} is not one of: {allowed}")

    @staticmethod
    def _subject_key(row: ReleaseRecord) -> tuple[str, str, str, str]:
        return row.tenant_id, row.organization_id, row.workspace_id, row.subject_id

    @staticmethod
    def _transition_release(
        session: AsyncSession,
        row: ReleaseRecord,
        target: ReleaseState,
        actor_id: str,
        correlation_id: str,
        *,
        event_type: str = "release.transitioned",
        reason: str = "Authoritative unified lifecycle transition",
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
                actor_kind="HUMAN",
                correlation_id=correlation_id,
                outcome=target.value,
                reason=reason,
                event_metadata={"from": previous, "to": target.value},
                occurred_at=now,
            )
        )

    @staticmethod
    def _transition_registry(
        session: AsyncSession,
        row: RegistryDefinitionRecord,
        target: RegistryState,
        actor_id: str,
        correlation_id: str,
        *,
        event_type: str,
        release_id: str,
    ) -> None:
        previous = row.lifecycle_state
        row.lifecycle_state = target.value
        row.release_id = release_id
        row.correlation_id = correlation_id
        session.add(
            AuditRecord(
                event_type=event_type,
                entity_type="agent",
                entity_id=row.subject_id,
                tenant_id=row.tenant_id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                actor_id=actor_id,
                actor_kind="HUMAN",
                correlation_id=correlation_id,
                outcome=target.value,
                reason="Authoritative unified lifecycle transition",
                event_metadata={
                    "from": previous,
                    "to": target.value,
                    "version": row.version,
                    "digest": row.digest,
                    "release_id": release_id,
                },
                occurred_at=datetime.now(UTC),
            )
        )

    async def _publish(self, rows: list[RegistryDefinitionRecord]) -> None:
        for row in rows:
            await self._registry.publish_committed(SqlRegistryStore.entry_from_record(row))

    @staticmethod
    def _governed(row: ReleaseRecord) -> GovernedRelease:
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
