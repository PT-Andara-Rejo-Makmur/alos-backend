from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.persistence.models import AuditRecord, RegistryDefinitionRecord, ReleaseRecord
from alos.persistence.registry import SqlRegistryStore
from alos.registry import RegistryConflictError, RegistryState
from alos.releases import (
    GovernedAgentLifecycle,
    PersistentReleaseAuthority,
    ReleaseConflictError,
    ReleaseState,
)

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL",
        "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test",
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _database(name: str) -> str:
    url = _database_url(name)
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    return url


def _registry(
    sessions: async_sessionmaker,
) -> AgentRegistry:
    return AgentRegistry(
        CanonicalContractCatalog(CONTRACTS_ROOT),
        InMemoryAuditRepository(),
        store=SqlRegistryStore(sessions),
        release_governed=True,
    )


async def _seed_version(
    sessions: async_sessionmaker,
    *,
    suffix: str,
    version: str,
    release_state: ReleaseState = ReleaseState.RELEASED,
    registry_state: RegistryState = RegistryState.DRAFT,
    organization_id: str = "org_unified",
    registry_tenant_id: str = "tenant_unified",
    registry_workspace_id: str = "workspace_unified",
    registry_subject_id: str = "agent.unified",
    registry_version: str | None = None,
) -> None:
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            ReleaseRecord(
                release_id=f"release_{suffix}",
                review_id=f"review_{suffix}",
                state=release_state.value,
                decided_by="actor_release",
                decided_at=now,
                created_by="actor_maker",
                created_at=now,
                updated_at=now,
                state_version=8,
                tenant_id="tenant_unified",
                organization_id="org_unified",
                workspace_id="workspace_unified",
                subject_id="agent.unified",
                subject_version=version,
                materiality="NON_MATERIAL",
                correlation_id=f"corr_{suffix}",
                kill_switch_active=False,
                ever_released=True,
            )
        )
        session.add(
            RegistryDefinitionRecord(
                tenant_id=registry_tenant_id,
                organization_id=organization_id,
                workspace_id=registry_workspace_id,
                subject_type="agent",
                subject_id=registry_subject_id,
                version=registry_version or version,
                contract_payload={
                    "agent_id": "agent.unified",
                    "agent_version": registry_version or version,
                },
                digest=f"digest_{suffix}",
                lifecycle_state=registry_state.value,
                created_by="actor_maker",
                correlation_id=f"corr_{suffix}",
                created_at=now,
            )
        )


async def _states(
    sessions: async_sessionmaker, suffix: str
) -> tuple[str, str, str | None, bool]:
    async with sessions() as session:
        release = await session.get(ReleaseRecord, f"release_{suffix}")
        registry = (
            await session.execute(
                select(RegistryDefinitionRecord).where(
                    RegistryDefinitionRecord.digest == f"digest_{suffix}",
                )
            )
        ).scalar_one()
        assert release is not None
        return (
            release.state,
            registry.lifecycle_state,
            registry.release_id,
            release.kill_switch_active,
        )


@pytest.mark.asyncio
async def test_unified_activation_kill_successor_rollback_restart_and_audit() -> None:
    url = await _database("alos_unified_lifecycle")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    registry = _registry(sessions)
    lifecycle = GovernedAgentLifecycle(sessions, registry)
    try:
        await _seed_version(sessions, suffix="v1", version="1.0.0")
        await registry.hydrate()
        with pytest.raises(ReleaseConflictError, match="registry version is not ACTIVE"):
            await lifecycle.runtime_entry(
                tenant_id="tenant_unified",
                organization_id="org_unified",
                workspace_id="workspace_unified",
                subject_id="agent.unified",
                version="1.0.0",
            )
        with pytest.raises(RegistryConflictError, match="governed release orchestration"):
            await registry.activate(
                tenant_id="tenant_unified",
                workspace_id="workspace_unified",
                subject_id="agent.unified",
                version="1.0.0",
                actor_id="bypass_actor",
                release_id="release_v1",
                correlation_id="corr_bypass",
            )
        governed_authority = PersistentReleaseAuthority(
            sessions, registry_governed=True
        )
        with pytest.raises(ReleaseConflictError, match="unified registry orchestration"):
            await governed_authority.activate(
                "release_v1", actor_id="bypass_actor", correlation_id="corr_bypass"
            )

        active = await lifecycle.activate(
            "release_v1", actor_id="actor_it", correlation_id="corr_activate_v1"
        )
        assert active.state is ReleaseState.ACTIVE
        assert await _states(sessions, "v1") == (
            "ACTIVE",
            "ACTIVE",
            "release_v1",
            False,
        )
        assert (
            await lifecycle.runtime_entry(
                tenant_id="tenant_unified",
                organization_id="org_unified",
                workspace_id="workspace_unified",
                subject_id="agent.unified",
                version="1.0.0",
            )
        ).state is RegistryState.ACTIVE

        await _seed_version(sessions, suffix="v2", version="2.0.0")
        await lifecycle.activate(
            "release_v2", actor_id="actor_it", correlation_id="corr_activate_v2"
        )
        killed = await lifecycle.activate_kill_switch(
            "release_v2",
            actor_id="actor_it",
            reason="Contain risk.",
            correlation_id="corr_kill_v2",
        )
        assert killed.state is ReleaseState.SUSPENDED
        assert await _states(sessions, "v2") == (
            "SUSPENDED",
            "SUSPENDED",
            "release_v2",
            True,
        )
        with pytest.raises(ReleaseConflictError, match="registry version is not ACTIVE"):
            await lifecycle.runtime_entry(
                tenant_id="tenant_unified",
                organization_id="org_unified",
                workspace_id="workspace_unified",
                subject_id="agent.unified",
                version="2.0.0",
            )

        await _seed_version(sessions, suffix="v3", version="3.0.0")
        await lifecycle.activate(
            "release_v3", actor_id="actor_it", correlation_id="corr_activate_v3"
        )
        with pytest.raises(ReleaseConflictError, match="active successor exists"):
            await lifecycle.clear_kill_switch(
                "release_v2",
                actor_id="actor_it",
                reason="Attempt stale recovery.",
                correlation_id="corr_clear_v2",
            )

        rolled_back = await lifecycle.rollback(
            "release_v3",
            target_release_id="release_v1",
            actor_id="actor_it",
            reason="Restore known good version.",
            correlation_id="corr_rollback",
        )
        assert rolled_back.state is ReleaseState.ROLLED_BACK
        assert (await _states(sessions, "v3"))[:2] == (
            "ROLLED_BACK",
            "SUSPENDED",
        )
        assert (await _states(sessions, "v1"))[:3] == (
            "ACTIVE",
            "ACTIVE",
            "release_v1",
        )

        restarted_registry = _registry(sessions)
        await restarted_registry.hydrate()
        restarted = GovernedAgentLifecycle(sessions, restarted_registry)
        runtime_entry = await restarted.runtime_entry(
            tenant_id="tenant_unified",
            organization_id="org_unified",
            workspace_id="workspace_unified",
            subject_id="agent.unified",
            version="1.0.0",
        )
        assert runtime_entry.release_id == "release_v1"
        async with sessions() as session:
            rollback_audits = await session.scalar(
                select(func.count())
                .select_from(AuditRecord)
                .where(AuditRecord.correlation_id == "corr_rollback")
            )
        assert rollback_audits == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unified_mismatch_and_registry_failure_leave_no_partial_state() -> None:
    url = await _database("alos_unified_atomic_failure")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    lifecycle = GovernedAgentLifecycle(sessions, _registry(sessions))
    try:
        mismatches = (
            ("tenant", "4.0.0", {"registry_tenant_id": "tenant_other"}),
            ("organization", "4.1.0", {"organization_id": "org_other"}),
            ("workspace", "4.2.0", {"registry_workspace_id": "workspace_other"}),
            ("subject", "4.3.0", {"registry_subject_id": "agent.other"}),
            ("version", "4.4.0", {"registry_version": "4.4.1"}),
        )
        for suffix, version, overrides in mismatches:
            await _seed_version(
                sessions,
                suffix=f"mismatch_{suffix}",
                version=version,
                **overrides,
            )
            with pytest.raises(ReleaseConflictError, match="exactly one"):
                await lifecycle.activate(
                    f"release_mismatch_{suffix}",
                    actor_id="actor_it",
                    correlation_id=f"corr_mismatch_{suffix}",
                )
            assert (
                await _states(sessions, f"mismatch_{suffix}")
            )[:2] == ("RELEASED", "DRAFT")

        await _seed_version(
            sessions,
            suffix="retired",
            version="5.0.0",
            registry_state=RegistryState.RETIRED,
        )
        with pytest.raises(ReleaseConflictError, match="DRAFT or APPROVED"):
            await lifecycle.activate(
                "release_retired",
                actor_id="actor_it",
                correlation_id="corr_retired",
            )
        assert (await _states(sessions, "retired"))[:2] == (
            "RELEASED",
            "RETIRED",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_unified_activation_keeps_exactly_one_active_pair() -> None:
    url = await _database("alos_unified_concurrency")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    lifecycle = GovernedAgentLifecycle(sessions, _registry(sessions))
    try:
        await _seed_version(sessions, suffix="concurrent_v1", version="1.0.0")
        await _seed_version(sessions, suffix="concurrent_v2", version="2.0.0")
        await asyncio.gather(
            lifecycle.activate(
                "release_concurrent_v1",
                actor_id="actor_it",
                correlation_id="corr_concurrent_v1",
            ),
            lifecycle.activate(
                "release_concurrent_v2",
                actor_id="actor_it",
                correlation_id="corr_concurrent_v2",
            ),
        )
        async with sessions() as session:
            active_releases = await session.scalar(
                select(func.count())
                .select_from(ReleaseRecord)
                .where(ReleaseRecord.state == "ACTIVE")
            )
            active_registries = await session.scalar(
                select(func.count())
                .select_from(RegistryDefinitionRecord)
                .where(
                    RegistryDefinitionRecord.subject_type == "agent",
                    RegistryDefinitionRecord.lifecycle_state == "ACTIVE",
                )
            )
            active_pair = (
                await session.execute(
                    select(ReleaseRecord, RegistryDefinitionRecord)
                    .join(
                        RegistryDefinitionRecord,
                        RegistryDefinitionRecord.release_id == ReleaseRecord.release_id,
                    )
                    .where(
                        ReleaseRecord.state == "ACTIVE",
                        RegistryDefinitionRecord.lifecycle_state == "ACTIVE",
                    )
                )
            ).one()
        assert active_releases == 1
        assert active_registries == 1
        assert active_pair[0].subject_version == active_pair[1].version
    finally:
        await engine.dispose()
