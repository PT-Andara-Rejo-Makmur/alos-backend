from __future__ import annotations

from pathlib import Path

import pytest

from alos.agents.lifecycle import AgentRunAuthority, AuthoritativeRunStatus, RunAuthorityError
from alos.agents.registry import AgentRegistry
from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.registry import DecisionAuthority

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def agent_definition() -> dict[str, object]:
    return {
        "tenant_id": "tenant_h06",
        "organization_id": "org_h06",
        "workspace_id": "workspace_h06",
        "correlation_id": "corr_h06_agent",
        "agent_id": "agent_h06_parent",
        "agent_version": "1.0.0",
        "owner_actor_id": "actor_h06_owner",
        "name": "H06 delegation agent",
        "purpose": "Verify bounded child authority.",
        "risk_level": "LOW",
        "capability_ids": ["capability_h06"],
        "skill_refs": [],
        "model_policy_ref": "policy.h06",
        "tool_ids": ["diagnostic.echo"],
        "permission_refs": ["tools.diagnostic.execute"],
        "scope_refs": ["scope.diagnostic"],
        "delegation_policy": {"enabled": True, "max_depth": 2},
    }


def parent_run_request() -> dict[str, object]:
    return {
        "run_id": "run_h06_root",
        "root_run_id": "run_h06_root",
        "agent_id": "agent_h06_parent",
        "agent_version": "1.0.0",
        "capability_id": "capability_h06",
        "execution_context": {
            "tenant_id": "tenant_h06",
            "organization_id": "org_h06",
            "workspace_id": "workspace_h06",
            "actor_id": "actor_h06_owner",
            "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
            "permission_refs": ["tools.diagnostic.execute"],
            "scope_refs": ["scope.diagnostic"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_h06_root",
            "execution_budget": {
                "max_cost": 25,
                "max_tokens": 500,
                "max_steps": 4,
                "max_tool_calls": 5,
            },
        },
        "input": {"message": "root"},
        "requested_tool_ids": ["diagnostic.echo"],
    }


async def active_agent(contracts: CanonicalContractCatalog, audit: InMemoryAuditRepository):
    registry = AgentRegistry(contracts, audit)
    entry = await registry.register(
        agent_definition(),
        tenant_id="tenant_h06",
        organization_id="org_h06",
        workspace_id="workspace_h06",
        actor_id="actor_h06_owner",
        correlation_id="corr_h06_register",
    )
    entry = await registry.approve(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_h06_approver",
        decision_id="decision_h06_001",
        authority=DecisionAuthority.IT,
        correlation_id="corr_h06_approve",
    )
    return await registry.activate(
        tenant_id=entry.tenant_id,
        workspace_id=entry.workspace_id,
        subject_id=entry.subject_id,
        version=entry.version,
        actor_id="actor_h06_release",
        release_id="release_h06_001",
        correlation_id="corr_h06_activate",
    )


@pytest.mark.asyncio
async def test_child_run_lineage_and_tree_are_server_calculated() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    agent = await active_agent(contracts, audit)

    root = await authority.begin(parent_run_request(), agent=agent)
    child_payload = {
        "run_id": "run_h06_child_001",
        "root_run_id": "run_h06_root",
        "parent_run_id": root.run_id,
        "agent_id": "agent_h06_parent",
        "agent_version": "1.0.0",
        "capability_id": "capability_h06",
        "execution_context": {
            "tenant_id": "tenant_h06",
            "organization_id": "org_h06",
            "workspace_id": "workspace_h06",
            "actor_id": "actor_h06_owner",
            "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
            "permission_refs": ["tools.diagnostic.execute"],
            "scope_refs": ["scope.diagnostic"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_h06_child",
            "execution_budget": {
                "max_cost": 10,
                "max_tokens": 200,
                "max_steps": 2,
                "max_tool_calls": 2,
            },
        },
        "input": {"message": "child"},
        "requested_tool_ids": ["diagnostic.echo"],
    }
    child = await authority.begin(child_payload, agent=agent)

    assert child.parent_run_id == root.run_id
    assert child.root_run_id == root.run_id
    assert child.depth == 1
    tree = await authority.get_run_tree(root.run_id)
    assert tree["run_id"] == root.run_id
    assert tree["children"][0]["run_id"] == child.run_id


@pytest.mark.asyncio
async def test_child_scope_and_budget_inheritance_are_restricted() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    agent = await active_agent(contracts, audit)

    root = await authority.begin(parent_run_request(), agent=agent)
    bad = {
        "run_id": "run_h06_bad_child",
        "root_run_id": root.run_id,
        "parent_run_id": root.run_id,
        "agent_id": "agent_h06_parent",
        "agent_version": "1.0.0",
        "capability_id": "capability_h06",
        "execution_context": {
            "tenant_id": "tenant_h06",
            "organization_id": "org_h06",
            "workspace_id": "workspace_h06",
            "actor_id": "actor_h06_owner",
            "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
            "permission_refs": ["tools.diagnostic.execute", "admin.override"],
            "scope_refs": ["scope.admin"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_h06_bad",
            "execution_budget": {"max_cost": 100, "max_tokens": 500},
        },
        "input": {"message": "bad"},
        "requested_tool_ids": ["diagnostic.echo", "admin.tool"],
    }
    with pytest.raises(RunAuthorityError):
        await authority.begin(bad, agent=agent)


@pytest.mark.asyncio
async def test_child_requires_parent_delegation_policy_and_enforces_max_children() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    agent = await active_agent(contracts, audit)

    agent.payload["delegation_policy"] = {
        "max_depth": 2,
        "max_children": 1,
        "max_concurrency": 1,
        "max_retries": 1,
    }
    root = await authority.begin(parent_run_request(), agent=agent)

    child_one = await authority.begin(
        {
            "run_id": "run_h06_child_1",
            "root_run_id": root.run_id,
            "parent_run_id": root.run_id,
            "agent_id": "agent_h06_parent",
            "agent_version": "1.0.0",
            "capability_id": "capability_h06",
            "execution_context": {
                "tenant_id": "tenant_h06",
                "organization_id": "org_h06",
                "workspace_id": "workspace_h06",
                "actor_id": "actor_h06_owner",
                "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
                "permission_refs": ["tools.diagnostic.execute"],
                "scope_refs": ["scope.diagnostic"],
                "data_classification": "INTERNAL",
                "correlation_id": "corr_h06_child_1",
                "execution_budget": {"max_cost": 10, "max_tokens": 200},
            },
            "input": {"message": "child-one"},
            "requested_tool_ids": ["diagnostic.echo"],
        },
        agent=agent,
    )
    assert child_one.parent_run_id == root.run_id

    with pytest.raises(RunAuthorityError):
        await authority.begin(
            {
                "run_id": "run_h06_child_2",
                "root_run_id": root.run_id,
                "parent_run_id": root.run_id,
                "agent_id": "agent_h06_parent",
                "agent_version": "1.0.0",
                "capability_id": "capability_h06",
                "execution_context": {
                    "tenant_id": "tenant_h06",
                    "organization_id": "org_h06",
                    "workspace_id": "workspace_h06",
                    "actor_id": "actor_h06_owner",
                    "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
                    "permission_refs": ["tools.diagnostic.execute"],
                    "scope_refs": ["scope.diagnostic"],
                    "data_classification": "INTERNAL",
                    "correlation_id": "corr_h06_child_2",
                    "execution_budget": {"max_cost": 10, "max_tokens": 200},
                },
                "input": {"message": "child-two"},
                "requested_tool_ids": ["diagnostic.echo"],
            },
            agent=agent,
        )

    agent.payload.pop("delegation_policy", None)
    root_without_policy = parent_run_request()
    root_without_policy["run_id"] = "run_h06_root_no_policy"
    root_without_policy["root_run_id"] = "run_h06_root_no_policy"
    root_without_policy_run = await authority.begin(root_without_policy, agent=agent)

    with pytest.raises(RunAuthorityError):
        await authority.begin(
            {
                "run_id": "run_h06_orphan_child",
                "root_run_id": root_without_policy_run.run_id,
                "parent_run_id": root_without_policy_run.run_id,
                "agent_id": "agent_h06_parent",
                "agent_version": "1.0.0",
                "capability_id": "capability_h06",
                "execution_context": {
                    "tenant_id": "tenant_h06",
                    "organization_id": "org_h06",
                    "workspace_id": "workspace_h06",
                    "actor_id": "actor_h06_owner",
                    "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
                    "permission_refs": ["tools.diagnostic.execute"],
                    "scope_refs": ["scope.diagnostic"],
                    "data_classification": "INTERNAL",
                    "correlation_id": "corr_h06_orphan",
                    "execution_budget": {"max_cost": 5, "max_tokens": 100},
                },
                "input": {"message": "orphan"},
                "requested_tool_ids": ["diagnostic.echo"],
            },
            agent=agent,
        )


@pytest.mark.asyncio
async def test_retry_limit_and_final_cancellation_propagate_to_nested_children() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    agent = await active_agent(contracts, audit)

    agent.payload["delegation_policy"] = {
        "max_depth": 3,
        "max_children": 3,
        "max_concurrency": 3,
        "max_retries": 1,
    }
    root = await authority.begin(parent_run_request(), agent=agent)
    child = await authority.begin(
        {
            "run_id": "run_h06_retry_child",
            "root_run_id": root.run_id,
            "parent_run_id": root.run_id,
            "agent_id": "agent_h06_parent",
            "agent_version": "1.0.0",
            "capability_id": "capability_h06",
            "execution_context": {
                "tenant_id": "tenant_h06",
                "organization_id": "org_h06",
                "workspace_id": "workspace_h06",
                "actor_id": "actor_h06_owner",
                "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
                "permission_refs": ["tools.diagnostic.execute"],
                "scope_refs": ["scope.diagnostic"],
                "data_classification": "INTERNAL",
                "correlation_id": "corr_h06_retry_child",
                "execution_budget": {"max_cost": 12, "max_tokens": 200},
            },
            "input": {"message": "retry"},
            "requested_tool_ids": ["diagnostic.echo"],
        },
        agent=agent,
    )
    grandchild = await authority.begin(
        {
            "run_id": "run_h06_retry_grandchild",
            "root_run_id": root.run_id,
            "parent_run_id": child.run_id,
            "agent_id": "agent_h06_parent",
            "agent_version": "1.0.0",
            "capability_id": "capability_h06",
            "execution_context": {
                "tenant_id": "tenant_h06",
                "organization_id": "org_h06",
                "workspace_id": "workspace_h06",
                "actor_id": "actor_h06_owner",
                "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
                "permission_refs": ["tools.diagnostic.execute"],
                "scope_refs": ["scope.diagnostic"],
                "data_classification": "INTERNAL",
                "correlation_id": "corr_h06_retry_grandchild",
                "execution_budget": {"max_cost": 8, "max_tokens": 150},
            },
            "input": {"message": "grandchild"},
            "requested_tool_ids": ["diagnostic.echo"],
        },
        agent=agent,
    )

    first_retry = await authority.retry_child_run(child.run_id, reason="retry once", retryable=True)
    assert first_retry.retry_count == 1
    with pytest.raises(RunAuthorityError):
        await authority.retry_child_run(child.run_id, reason="retry again", retryable=True)

    cancelled = await authority.cancel(root.run_id, actor_id="actor_h06_owner", reason="cancel all")
    assert cancelled.status is AuthoritativeRunStatus.CANCELLED
    assert (await authority.get(child.run_id)).status is AuthoritativeRunStatus.CANCELLED
    assert (await authority.get(grandchild.run_id)).status is AuthoritativeRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_depth_and_parent_cancellation_propagate_to_children() -> None:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    authority = AgentRunAuthority(contracts=contracts, audit=audit)
    agent = await active_agent(contracts, audit)

    root = await authority.begin(parent_run_request(), agent=agent)
    child = await authority.begin(
        {
            "run_id": "run_h06_child_cancel",
            "root_run_id": root.run_id,
            "parent_run_id": root.run_id,
            "agent_id": "agent_h06_parent",
            "agent_version": "1.0.0",
            "capability_id": "capability_h06",
            "execution_context": {
                "tenant_id": "tenant_h06",
                "organization_id": "org_h06",
                "workspace_id": "workspace_h06",
                "actor_id": "actor_h06_owner",
                "authority_context": {"role": "parent_operator", "authority_level": "SYSTEM"},
                "permission_refs": ["tools.diagnostic.execute"],
                "scope_refs": ["scope.diagnostic"],
                "data_classification": "INTERNAL",
                "correlation_id": "corr_h06_child_cancel",
                "execution_budget": {"max_cost": 10, "max_tokens": 200},
            },
            "input": {"message": "child"},
            "requested_tool_ids": ["diagnostic.echo"],
        },
        agent=agent,
    )

    cancelled = await authority.cancel(root.run_id, actor_id="actor_h06_owner", reason="cancel all")
    assert cancelled.status is AuthoritativeRunStatus.CANCELLED
    child_after = await authority.get(child.run_id)
    assert child_after.status is not AuthoritativeRunStatus.RUNNING
