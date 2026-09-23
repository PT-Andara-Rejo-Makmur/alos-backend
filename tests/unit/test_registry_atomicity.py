from __future__ import annotations

from pathlib import Path

import pytest

from alos.audit import AuditEvent, InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.registry import (
    DecisionAuthority,
    InMemoryRegistryStore,
    RegistryEntry,
    RegistryNotFoundError,
    RegistryState,
)
from alos.skills.registry import SkillRegistry

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


class PersistenceFailure(RuntimeError):
    pass


class FailureInjectingStore:
    def __init__(self, trace: list[str]) -> None:
        self._delegate = InMemoryRegistryStore()
        self._trace = trace
        self.fail = False

    async def save(self, entry: RegistryEntry) -> None:
        self._trace.append(f"save:{entry.state.value}")
        if self.fail:
            raise PersistenceFailure(f"save rejected for {entry.state.value}")
        await self._delegate.save(entry)

    async def load_subject_type(self, subject_type: str) -> tuple[RegistryEntry, ...]:
        return await self._delegate.load_subject_type(subject_type)


class TracingAudit:
    def __init__(self, trace: list[str]) -> None:
        self._delegate = InMemoryAuditRepository()
        self._trace = trace

    async def append(self, event: AuditEvent) -> None:
        self._trace.append(f"audit:{event.event_type}")
        await self._delegate.append(event)

    def event_types(self) -> tuple[str, ...]:
        return tuple(
            event.event_type for event in self._delegate.list_events(tenant_id="tenant_atomicity")
        )


def skill_payload() -> dict[str, object]:
    return {
        "skill_id": "skill.registry.atomicity",
        "skill_version": "1.0.0",
        "name": "Registry atomicity",
        "description": "Prove persistent state is published before cache state.",
        "input_schema_ref": "https://schemas.alos.dev/example/input.json",
        "output_schema_ref": "https://schemas.alos.dev/example/output.json",
        "required_tool_ids": [],
    }


def registry() -> tuple[SkillRegistry, FailureInjectingStore, TracingAudit, list[str]]:
    trace: list[str] = []
    store = FailureInjectingStore(trace)
    audit = TracingAudit(trace)
    return (
        SkillRegistry(CanonicalContractCatalog(CONTRACTS_ROOT), audit, store=store),
        store,
        audit,
        trace,
    )


async def register_draft(target: SkillRegistry) -> RegistryEntry:
    return await target.register(
        skill_payload(),
        tenant_id="tenant_atomicity",
        organization_id="organization_atomicity",
        workspace_id="workspace_atomicity",
        actor_id="actor_atomicity",
        correlation_id="corr_atomicity",
    )


async def approve(target: SkillRegistry) -> RegistryEntry:
    return await target.approve(
        tenant_id="tenant_atomicity",
        workspace_id="workspace_atomicity",
        subject_id="skill.registry.atomicity",
        version="1.0.0",
        actor_id="actor_it_atomicity",
        decision_id="decision_atomicity",
        authority=DecisionAuthority.IT,
        correlation_id="corr_atomicity",
    )


def cached(target: SkillRegistry) -> RegistryEntry:
    return target.get(
        tenant_id="tenant_atomicity",
        workspace_id="workspace_atomicity",
        subject_id="skill.registry.atomicity",
        version="1.0.0",
    )


@pytest.mark.asyncio
async def test_failed_register_does_not_publish_cache_or_success_audit() -> None:
    target, store, audit, _trace = registry()
    store.fail = True

    with pytest.raises(PersistenceFailure):
        await register_draft(target)

    with pytest.raises(RegistryNotFoundError):
        cached(target)
    assert audit.event_types() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transition", "expected_state", "forbidden_event"),
    [
        ("approve", RegistryState.DRAFT, "registry.version.approved"),
        ("activate", RegistryState.APPROVED, "registry.version.activated"),
    ],
)
async def test_failed_transition_keeps_previous_cached_state_and_emits_no_success_audit(
    transition: str,
    expected_state: RegistryState,
    forbidden_event: str,
) -> None:
    target, store, audit, _trace = registry()
    await register_draft(target)
    if transition == "activate":
        await approve(target)
    store.fail = True

    with pytest.raises(PersistenceFailure):
        if transition == "approve":
            await approve(target)
        else:
            await target.activate(
                tenant_id="tenant_atomicity",
                workspace_id="workspace_atomicity",
                subject_id="skill.registry.atomicity",
                version="1.0.0",
                actor_id="actor_release_atomicity",
                release_id="release_atomicity",
                correlation_id="corr_atomicity",
            )

    assert cached(target).state is expected_state
    assert forbidden_event not in audit.event_types()


@pytest.mark.asyncio
async def test_success_persists_before_cache_success_is_audited() -> None:
    target, _store, audit, trace = registry()

    await register_draft(target)
    await approve(target)
    active = await target.activate(
        tenant_id="tenant_atomicity",
        workspace_id="workspace_atomicity",
        subject_id="skill.registry.atomicity",
        version="1.0.0",
        actor_id="actor_release_atomicity",
        release_id="release_atomicity",
        correlation_id="corr_atomicity",
    )

    assert active.state is RegistryState.ACTIVE
    assert cached(target).state is RegistryState.ACTIVE
    assert trace == [
        "save:DRAFT",
        "audit:registry.version.created",
        "save:APPROVED",
        "audit:registry.version.approved",
        "save:ACTIVE",
        "audit:registry.version.activated",
    ]
    assert audit.event_types() == (
        "registry.version.activated",
        "registry.version.approved",
        "registry.version.created",
    )
