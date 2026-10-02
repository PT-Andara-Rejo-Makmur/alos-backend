from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from alos.audit import InMemoryAuditRepository
from alos.identity import DataScope, Principal
from alos.memory import MemoryEvidenceBundle, MemoryRecord, MemoryRepository
from alos.research.models import FindingKind, ResearchFinding


@pytest.fixture
def principal() -> Principal:
    return Principal(
        actor_id="actor_memory_reader",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        permissions=frozenset({"memory.read", "research.read"}),
        scopes=frozenset({"scope.workspace.001", "scope.project.alpha"}),
        roles=frozenset({"DIVISION_MEMBER"}),
        data_scope=DataScope.PROJECT,
        division_id="division_01",
        project_id="project_alpha",
        active=True,
    )


def test_active_memory_is_returned_for_authorized_scope(principal: Principal) -> None:
    repo = MemoryRepository(audit=InMemoryAuditRepository())
    now = datetime.now(UTC)
    record = MemoryRecord(
        memory_id="memory_001",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        division_id="division_01",
        project_id="project_alpha",
        actor_id="actor_memory_writer",
        scope_refs=("scope.workspace.001", "scope.project.alpha"),
        classification="INTERNAL",
        content="Authorized internal memory",
        source_ref="source_001",
        evidence_ref="evidence_001",
        run_id="run_001",
        correlation_id="corr_001",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    repo.write(record)
    found = repo.retrieve(principal=principal, query="authorized")

    assert len(found) == 1
    assert found[0].memory_id == "memory_001"


def test_expired_memory_is_excluded(principal: Principal) -> None:
    repo = MemoryRepository(audit=InMemoryAuditRepository())
    now = datetime.now(UTC)
    repo.write(
        MemoryRecord(
            memory_id="memory_expired",
            tenant_id="tenant_001",
            organization_id="org_001",
            workspace_id="workspace_001",
            division_id="division_01",
            project_id="project_alpha",
            actor_id="actor_memory_writer",
            scope_refs=("scope.workspace.001", "scope.project.alpha"),
            classification="INTERNAL",
            content="expired memory",
            source_ref="source_002",
            evidence_ref="evidence_002",
            run_id="run_002",
            correlation_id="corr_002",
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(minutes=1),
        )
    )

    assert repo.retrieve(principal=principal, query="expired") == []


def test_cross_scope_memory_is_excluded(principal: Principal) -> None:
    repo = MemoryRepository(audit=InMemoryAuditRepository())
    now = datetime.now(UTC)
    repo.write(
        MemoryRecord(
            memory_id="memory_other_project",
            tenant_id="tenant_001",
            organization_id="org_001",
            workspace_id="workspace_001",
            division_id="division_01",
            project_id="project_beta",
            actor_id="actor_other",
            scope_refs=("scope.project.beta",),
            classification="INTERNAL",
            content="wrong project memory",
            source_ref="source_003",
            evidence_ref="evidence_003",
            run_id="run_003",
            correlation_id="corr_003",
            created_at=now,
            expires_at=now + timedelta(days=10),
        )
    )

    assert repo.retrieve(principal=principal, query="wrong") == []


def test_memory_evidence_bundle_includes_lineage() -> None:
    bundle = MemoryEvidenceBundle(
        memory_refs=["memory_001"],
        source_refs=["source_001"],
        evidence_refs=["evidence_001"],
        run_id="run_001",
        correlation_id="corr_001",
        classification="INTERNAL",
        freshness="CURRENT",
    )

    assert bundle.run_id == "run_001"
    assert "source_001" in bundle.source_refs


def test_research_finding_keeps_lineage_and_type() -> None:
    finding = ResearchFinding(
        finding_id="finding_001",
        kind=FindingKind.RESEARCH,
        domain="TECHNOLOGY",
        statement="Evidence-backed finding",
        evidence_refs=("evidence_001", "evidence_002"),
        confidence=0.92,
    )

    assert finding.kind == FindingKind.RESEARCH
    assert finding.evidence_refs[0] == "evidence_001"
