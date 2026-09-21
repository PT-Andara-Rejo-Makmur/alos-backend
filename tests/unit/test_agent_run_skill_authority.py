from __future__ import annotations

from pathlib import Path

import pytest

from alos.agents.lifecycle import AgentRunAuthority, RunAuthorityError
from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.registry import DecisionAuthority, RegistryEntry
from alos.skills.registry import SkillRegistry

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"
SKILL_REF = {"skill_id": "skill.run.delta", "skill_version": "1.0.0"}


def skill_payload(
    *,
    version: str = "1.0.0",
    permissions: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "skill_id": "skill.run.delta",
        "skill_version": version,
        "name": "Run authority delta",
        "description": "Verify exact Backend-authorized run Skill refs.",
        "input_schema_ref": "https://schemas.alos.dev/example/input.json",
        "output_schema_ref": "https://schemas.alos.dev/example/output.json",
        "permission_refs": list(permissions),
        "scope_refs": list(scopes),
        "required_tool_ids": list(tools),
    }


def agent_payload(
    *,
    skill_refs: list[dict[str, str]] | None = None,
    permissions: tuple[str, ...] = ("permission.a",),
    scopes: tuple[str, ...] = ("scope.x",),
    tools: tuple[str, ...] = ("tool.a",),
) -> dict[str, object]:
    return {
        "tenant_id": "tenant_run_delta",
        "organization_id": "organization_run_delta",
        "workspace_id": "workspace_run_delta",
        "agent_id": "agent.run.delta",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_run_delta",
        "name": "Run authority delta",
        "purpose": "Verify exact authorized Skill refs.",
        "risk_level": "LOW",
        "capability_ids": ["capability.run.delta"],
        "skill_refs": [SKILL_REF] if skill_refs is None else skill_refs,
        "model_policy_ref": "model-policy.run-delta",
        "tool_ids": list(tools),
        "permission_refs": list(permissions),
        "scope_refs": list(scopes),
        "delegation_policy": {"enabled": False, "max_depth": 0},
    }


def run_request(
    *,
    authorized_skill_refs: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": "run_skill_delta",
        "root_run_id": "run_skill_delta",
        "agent_id": "agent.run.delta",
        "agent_version": "1.0.0",
        "capability_id": "capability.run.delta",
        "execution_context": {
            "tenant_id": "tenant_run_delta",
            "organization_id": "organization_run_delta",
            "workspace_id": "workspace_run_delta",
            "actor_id": "actor_run_delta",
            "authority_context": {"role": "runner", "authority_level": "SYSTEM"},
            "permission_refs": ["permission.a", "permission.b"],
            "allowed_tool_ids": ["tool.a", "tool.b"],
            "scope_refs": ["scope.x", "scope.y"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_run_skill_delta",
        },
        "input": {"question": "run"},
        "requested_tool_ids": [],
    }
    if authorized_skill_refs is not None:
        payload["authorized_skill_refs"] = authorized_skill_refs
    return payload


async def transition_active(registry: object, payload: dict[str, object]) -> RegistryEntry:
    entry = await registry.register(  # type: ignore[attr-defined]
        payload,
        tenant_id="tenant_run_delta",
        organization_id="organization_run_delta",
        workspace_id="workspace_run_delta",
        actor_id="actor_run_delta",
        correlation_id="corr_run_skill_delta",
    )
    entry = await registry.approve(  # type: ignore[attr-defined]
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_it_delta",
        decision_id=f"decision.{entry.subject_id}",
        authority=DecisionAuthority.IT,
        correlation_id="corr_run_skill_delta",
    )
    return await registry.activate(  # type: ignore[attr-defined,no-any-return]
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_release_delta",
        release_id=f"release.{entry.subject_id}",
        correlation_id="corr_run_skill_delta",
    )


async def authority_setup(
    *,
    skill: dict[str, object] | None = None,
    agent: dict[str, object] | None = None,
    activate_skill: bool = True,
) -> tuple[AgentRunAuthority, RegistryEntry]:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    skills = SkillRegistry(contracts, audit)
    skill_definition = skill or skill_payload()
    if activate_skill:
        await transition_active(skills, skill_definition)
    else:
        await skills.register(
            skill_definition,
            tenant_id="tenant_run_delta",
            organization_id="organization_run_delta",
            workspace_id="workspace_run_delta",
            actor_id="actor_run_delta",
            correlation_id="corr_run_skill_delta",
        )
    agents = AgentRegistry(contracts, audit)
    active_agent = await transition_active(agents, agent or agent_payload())
    return AgentRunAuthority(contracts=contracts, audit=audit, skills=skills), active_agent


@pytest.mark.asyncio
async def test_valid_exact_skill_ref_is_derived_and_auditable() -> None:
    authority, agent = await authority_setup()
    started = await authority.begin(run_request(), agent=agent)
    snapshot = await authority.runtime_authorization(started.run_id)

    assert started.authorized_skill_refs == (("skill.run.delta", "1.0.0"),)
    assert started.request["authorized_skill_refs"] == [SKILL_REF]
    assert snapshot["authorized_skill_refs"] == [SKILL_REF]


@pytest.mark.asyncio
async def test_injected_skill_ref_absent_from_agent_is_rejected() -> None:
    authority, agent = await authority_setup(agent=agent_payload(skill_refs=[]))
    with pytest.raises(RunAuthorityError) as raised:
        await authority.begin(run_request(authorized_skill_refs=[SKILL_REF]), agent=agent)
    assert raised.value.code == "SKILL_REF_NOT_ASSIGNED"


@pytest.mark.asyncio
async def test_inactive_skill_is_rejected() -> None:
    authority, agent = await authority_setup(activate_skill=False)
    with pytest.raises(RunAuthorityError) as raised:
        await authority.begin(run_request(), agent=agent)
    assert raised.value.code == "SKILL_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_wrong_exact_version_has_no_fallback() -> None:
    wrong_ref = {"skill_id": "skill.run.delta", "skill_version": "2.0.0"}
    authority, agent = await authority_setup(agent=agent_payload(skill_refs=[wrong_ref]))
    with pytest.raises(RunAuthorityError) as raised:
        await authority.begin(run_request(), agent=agent)
    assert raised.value.code == "SKILL_VERSION_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill", "agent", "code"),
    [
        (
            skill_payload(permissions=("permission.b",)),
            agent_payload(permissions=("permission.a",)),
            "SKILL_PERMISSION_MISMATCH",
        ),
        (
            skill_payload(scopes=("scope.y",)),
            agent_payload(scopes=("scope.x",)),
            "SKILL_SCOPE_MISMATCH",
        ),
        (
            skill_payload(tools=("tool.b",)),
            agent_payload(tools=("tool.a",)),
            "SKILL_TOOL_MISMATCH",
        ),
    ],
)
async def test_skill_prerequisites_use_effective_agent_run_authority(
    skill: dict[str, object],
    agent: dict[str, object],
    code: str,
) -> None:
    authority, active_agent = await authority_setup(skill=skill, agent=agent)
    with pytest.raises(RunAuthorityError) as raised:
        await authority.begin(run_request(), agent=active_agent)
    assert raised.value.code == code
