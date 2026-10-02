"""ARA-specific authority and memory boundaries, independent of organizational shared memory."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alos.ara.orchestration import AraOrchestrator
from alos.context.bundle import ContextBuildRequest, ContextBundleBuilder
from alos.identity import DataScope, Principal
from alos.memory import MemoryRecord, MemoryService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    (
        None,
        "evidence_id",
        "source_id",
        "run_id",
        "correlation_id",
        "tenant_id",
        "organization_id",
        "workspace_id",
        "validation_status",
        "instruction_authority",
        "data_classification",
        "origin_actor",
        "origin_thread",
        "origin_status",
        "origin_tenant",
        "origin_organization",
        "origin_workspace",
    ),
)
async def test_conversation_memory_requires_registered_evidence_and_origin(
    mismatch: str | None,
) -> None:
    actor = principal()
    memory = MemoryService()
    record = MemoryRecord(
        memory_id="memory_lineage",
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        actor_id=actor.actor_id,
        division_id=actor.division_id,
        project_id=actor.project_id,
        scope_refs=("scope.ara",),
        content="Context only",
        created_at=datetime.now(UTC),
        source_ref="source_ara",
        evidence_ref="evidence_ara",
        run_id="run_ara",
        correlation_id="corr_ara",
        metadata={"thread_id": "thread_ara"},
    )
    memory.write(record=record)
    evidence = {
        "evidence_id": "evidence_ara",
        "source_id": "source_ara",
        "run_id": "run_ara",
        "correlation_id": "corr_ara",
        "tenant_id": actor.tenant_id,
        "organization_id": actor.organization_id,
        "workspace_id": actor.workspace_id,
        "validation_status": "VALID",
        "instruction_authority": False,
        "data_classification": "INTERNAL",
    }
    origin = SimpleNamespace(
        actor_id=actor.actor_id,
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        status=SimpleNamespace(value="COMPLETED"),
        request={"input": {"thread_id": "thread_ara"}},
    )
    if mismatch and mismatch.startswith("origin_"):
        key = mismatch.removeprefix("origin_")
        if key == "thread":
            origin.request["input"]["thread_id"] = "outside"
        elif key == "status":
            origin.status.value = "FAILED"
        else:
            setattr(
                origin,
                {
                    "actor": "actor_id",
                    "tenant": "tenant_id",
                    "organization": "organization_id",
                    "workspace": "workspace_id",
                }[key],
                "outside",
            )
    elif mismatch:
        evidence[mismatch] = True if mismatch == "instruction_authority" else "outside"
    orchestrator = AraOrchestrator(
        repository=None,
        contracts=None,
        authority=SimpleNamespace(get=AsyncMock(return_value=origin)),
        genesis=None,
        registry=None,
        audit=None,
        test_enabled=True,
        memory=memory,
        evidence_registry=SimpleNamespace(get=AsyncMock(return_value=evidence)),
    )
    admitted = await orchestrator.conversation_memory(actor, "thread_ara")
    assert admitted == ([record] if mismatch is None else [])


def principal() -> Principal:
    return Principal(
        actor_id="actor_ara",
        tenant_id="tenant_ara",
        organization_id="org_ara",
        workspace_id="workspace_ara",
        scopes=frozenset({"scope.ara"}),
        permissions=frozenset({"sales.read"}),
        roles=frozenset({"DIVISION_MEMBER"}),
        data_scope=DataScope.WORKSPACE,
        division_id="division_sales",
        project_id="project_ara",
    )


@pytest.mark.parametrize(
    "boundary",
    ("tenant_id", "organization_id", "workspace_id", "actor_id", "division_id", "project_id"),
)
def test_conversation_memory_never_crosses_boundary(boundary: str) -> None:
    actor = principal()
    memory = MemoryService()
    record = MemoryRecord(
        memory_id="memory_ara",
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        actor_id=actor.actor_id,
        division_id=actor.division_id,
        project_id=actor.project_id,
        scope_refs=("scope.ara",),
        content="Context only",
        created_at=datetime.now(UTC),
        source_ref="source_ara",
        evidence_ref="evidence_ara",
        run_id="run_ara",
        correlation_id="corr_ara",
        metadata={"thread_id": "thread_ara"},
    )
    memory.write(record=record)
    assert memory.retrieve_conversation(principal=actor, thread_id="thread_ara") == [record]
    changed = replace(actor, **{boundary: "outside"})
    assert memory.retrieve_conversation(principal=changed, thread_id="thread_ara") == []
    assert memory.retrieve_conversation(principal=actor, thread_id="thread_other") == []


@pytest.mark.parametrize("case", ("expired", "classification", "scope", "unbacked"))
def test_invalid_conversation_memory_is_not_admitted(case: str) -> None:
    actor = principal()
    memory = MemoryService()
    data = dict(
        memory_id="memory_invalid",
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        actor_id=actor.actor_id,
        scope_refs=("scope.ara",),
        content="Must not be admitted",
        created_at=datetime.now(UTC),
        source_ref="source_ara",
        evidence_ref="evidence_ara",
        run_id="run_ara",
        correlation_id="corr_ara",
        metadata={"thread_id": "thread_ara"},
    )
    if case == "expired":
        data["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    elif case == "classification":
        data["classification"] = "RESTRICTED"
    elif case == "scope":
        data["scope_refs"] = ("scope.outside",)
    else:
        data["evidence_ref"] = None
    memory.write(record=MemoryRecord.model_validate(data))
    assert memory.retrieve_conversation(principal=actor, thread_id="thread_ara") == []


@pytest.mark.parametrize("boundary", ("division_id", "project_id"))
def test_context_cannot_override_principal_scope(boundary: str) -> None:
    actor = principal()
    result = ContextBundleBuilder().build(
        actor, request=ContextBuildRequest(**{boundary: "outside"}), correlation_id="corr_ara"
    )
    assert result.status == "DENIED" and not result.allowed_tools
    assert getattr(result, boundary) == getattr(actor, boundary)


def test_role_names_never_grant_restricted_classification() -> None:
    actor = replace(principal(), roles=frozenset({"EXECUTIVE", "IT_ADMIN", "SUPER_ADMIN"}))
    builder = ContextBundleBuilder()
    assert (
        builder.build(
            actor, request=ContextBuildRequest(), correlation_id="corr_ara"
        ).data_classification
        == "INTERNAL"
    )
    granted = replace(actor, permissions=frozenset({"restricted.access"}))
    assert (
        builder.build(
            granted, request=ContextBuildRequest(), correlation_id="corr_ara"
        ).data_classification
        == "RESTRICTED"
    )
