from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alos.governance.gates import (
    AssuranceEvaluator,
    AutomatedAssuranceReport,
    ExpectedBehavior,
    ObservedBehavior,
    TestCategory,
)
from alos.governance.materiality import Materiality
from alos.persistence.models import AuditRecord, ReleaseLifecycleEventRecord
from alos.releases import PersistentReleaseAuthority, ReleaseConflictError, ReleaseState
from alos.reviews.decisions import AuthoritativeDecision, AuthorityLevel, DecisionOutcome
from alos.reviews.packages import ReviewPackageReference


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


def _assurance() -> AutomatedAssuranceReport:
    check = AssuranceEvaluator().evaluate(
        test_id="persistent_release",
        category=TestCategory.POSITIVE,
        expected=ExpectedBehavior(status="SUCCESS"),
        observed=ObservedBehavior(status="SUCCESS"),
    )
    return AutomatedAssuranceReport(
        checks=(check,), required_categories=frozenset({TestCategory.POSITIVE})
    )


def _package(review_id: str, version: str) -> ReviewPackageReference:
    return ReviewPackageReference(
        review_id=review_id,
        tenant_id="tenant_release",
        workspace_id="workspace_release",
        subject_id="agent.release",
        subject_version=version,
        contract_version="1.0.0",
        evidence_uri=f"urn:alos:evidence:{review_id}",
        recorded_at=datetime.now(UTC),
    )


def _decision(
    decision_id: str,
    review_id: str,
    *,
    actor_id: str = "actor_it",
    outcome: DecisionOutcome = DecisionOutcome.APPROVED,
) -> AuthoritativeDecision:
    return AuthoritativeDecision(
        decision_id=decision_id,
        review_id=review_id,
        authority=AuthorityLevel.IT,
        outcome=outcome,
        actor_id=actor_id,
        rationale="Persistent governed decision.",
        decided_at=datetime.now(UTC),
    )


async def _ready_for_it(
    authority: PersistentReleaseAuthority,
    release_id: str,
    review_id: str,
    version: str,
) -> None:
    correlation = f"corr_{release_id}"
    await authority.create(
        release_id=release_id,
        review_id=review_id,
        tenant_id="tenant_release",
        organization_id="org_release",
        workspace_id="workspace_release",
        subject_id="agent.release",
        subject_version=version,
        materiality=Materiality.NON_MATERIAL,
        actor_id="actor_maker",
        correlation_id=correlation,
    )
    await authority.mark_implemented(
        release_id, actor_id="actor_maker", correlation_id=correlation
    )
    with pytest.raises(ReleaseConflictError, match="maker cannot perform"):
        await authority.record_automated_assurance(
            release_id,
            _assurance(),
            actor_id="actor_maker",
            correlation_id=correlation,
        )
    await authority.record_automated_assurance(
        release_id,
        _assurance(),
        actor_id="actor_checker",
        correlation_id=correlation,
    )
    await authority.record_ai_review_package(
        release_id,
        _package(review_id, version),
        actor_id="genesis_review",
        correlation_id=correlation,
    )
    await authority.submit_for_it(
        release_id, actor_id="actor_checker", correlation_id=correlation
    )


@pytest.mark.asyncio
async def test_persistent_release_restart_replay_kill_rollback_and_audit() -> None:
    url = await _database("alos_release_persistence")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first = PersistentReleaseAuthority(sessions)
    try:
        await _ready_for_it(first, "release_v1", "review_v1", "1.0.0")
        with pytest.raises(ReleaseConflictError, match="checker cannot approve"):
            await first.record_it_decision(
                "release_v1",
                _decision("decision_checker", "review_v1", actor_id="actor_checker"),
                correlation_id="corr_release_v1",
            )
        await first.record_it_decision(
            "release_v1",
            _decision("decision_durable", "review_v1"),
            correlation_id="corr_release_v1",
        )
        await first.release(
            "release_v1", actor_id="actor_release", correlation_id="corr_release_v1"
        )
        await first.activate(
            "release_v1", actor_id="actor_release", correlation_id="corr_release_v1"
        )

        restarted = PersistentReleaseAuthority(sessions)
        assert (await restarted.get("release_v1")).state is ReleaseState.ACTIVE
        with pytest.raises(LookupError):
            await restarted.get(
                "release_v1", tenant_id="tenant_other", workspace_id="workspace_release"
            )

        await _ready_for_it(restarted, "release_v2", "review_v2", "2.0.0")
        with pytest.raises(ReleaseConflictError, match="already been used"):
            await restarted.record_it_decision(
                "release_v2",
                _decision("decision_durable", "review_v2"),
                correlation_id="corr_release_v2",
            )
        await restarted.record_it_decision(
            "release_v2",
            _decision("decision_v2", "review_v2"),
            correlation_id="corr_release_v2",
        )
        with pytest.raises(ReleaseConflictError):
            await restarted.record_it_decision(
                "release_v2",
                _decision("decision_duplicate_transition", "review_v2"),
                correlation_id="corr_release_v2",
            )
        await restarted.release(
            "release_v2", actor_id="actor_release", correlation_id="corr_release_v2"
        )
        await restarted.activate(
            "release_v2", actor_id="actor_release", correlation_id="corr_release_v2"
        )
        killed = await restarted.activate_kill_switch(
            "release_v2",
            actor_id="actor_it",
            reason="Contain production risk.",
            correlation_id="corr_kill",
        )
        assert killed.kill_switch_active is True
        after_kill_restart = PersistentReleaseAuthority(sessions)
        assert (await after_kill_restart.get("release_v2")).state is ReleaseState.SUSPENDED
        assert await after_kill_restart.execution_allowed("release_v2") is False
        await after_kill_restart.clear_kill_switch(
            "release_v2",
            actor_id="actor_it",
            reason="Containment complete.",
            correlation_id="corr_clear",
        )
        rolled_back = await after_kill_restart.rollback(
            "release_v2",
            target_release_id="release_v1",
            actor_id="actor_it",
            reason="Restore previous release.",
            correlation_id="corr_rollback",
        )
        final = PersistentReleaseAuthority(sessions)
        assert rolled_back.state is ReleaseState.ROLLED_BACK
        assert (await final.get("release_v1")).state is ReleaseState.ACTIVE
        assert (await final.get("release_v2")).rollback_target_release_id == "release_v1"

        await _ready_for_it(final, "release_returned", "review_returned", "3.0.0")
        returned = await final.record_it_decision(
            "release_returned",
            _decision(
                "decision_returned", "review_returned", outcome=DecisionOutcome.RETURNED
            ),
            correlation_id="corr_returned",
        )
        assert returned.state is ReleaseState.RETURNED
        with pytest.raises(ReleaseConflictError):
            await final.release(
                "release_returned",
                actor_id="actor_release",
                correlation_id="corr_returned",
            )
        await _ready_for_it(final, "release_rejected", "review_rejected", "4.0.0")
        rejected = await final.record_it_decision(
            "release_rejected",
            _decision(
                "decision_rejected", "review_rejected", outcome=DecisionOutcome.REJECTED
            ),
            correlation_id="corr_rejected",
        )
        assert rejected.state is ReleaseState.REJECTED
        with pytest.raises(ReleaseConflictError):
            await final.release(
                "release_rejected",
                actor_id="actor_release",
                correlation_id="corr_rejected",
            )

        async with sessions() as session:
            lifecycle_count = await session.scalar(
                select(func.count()).select_from(ReleaseLifecycleEventRecord)
            )
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditRecord)
                .where(AuditRecord.correlation_id == "corr_rollback")
            )
        assert lifecycle_count is not None and lifecycle_count >= 20
        assert audit_count == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_persistent_decisions_allow_exactly_one_transition() -> None:
    url = await _database("alos_release_concurrency")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    authority = PersistentReleaseAuthority(sessions)
    try:
        await _ready_for_it(authority, "release_concurrent", "review_concurrent", "1.0.0")
        results = await asyncio.gather(
            authority.record_it_decision(
                "release_concurrent",
                _decision("decision_concurrent_a", "review_concurrent"),
                correlation_id="corr_concurrent",
            ),
            authority.record_it_decision(
                "release_concurrent",
                _decision("decision_concurrent_b", "review_concurrent"),
                correlation_id="corr_concurrent",
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in results) == 1
        assert sum(isinstance(item, ReleaseConflictError) for item in results) == 1
        assert (await authority.get("release_concurrent")).state is ReleaseState.IT_APPROVED
    finally:
        await engine.dispose()
