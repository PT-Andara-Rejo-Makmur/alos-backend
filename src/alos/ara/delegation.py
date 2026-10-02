"""One bounded business reader child, issued by the existing Backend run authority."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from alos.agents.lifecycle import AgentRunAuthority
from alos.integrations.genesis import GenesisClient
from alos.registry import RegistryEntry, RegistryState
from alos.tools.business.catalog import BUSINESS_TOOLS


async def read_child(
    *,
    authority: AgentRunAuthority,
    genesis: GenesisClient,
    parent: dict[str, Any],
    parent_result: dict[str, Any],
    definition: dict[str, Any],
) -> dict[str, Any]:
    context = copy.deepcopy(parent["execution_context"])
    tool = next(
        (item for item in context["allowed_tool_ids"] if item != "executive.overview.read"),
        context["allowed_tool_ids"][0],
    )
    permission = BUSINESS_TOOLS[tool][3]
    if (
        BUSINESS_TOOLS[tool][1] == "overview"
        and "EXECUTIVE" in context["authority_context"]["role_refs"]
    ):
        permission = "strategy.read"
    context["allowed_tool_ids"] = [tool]
    context["permission_refs"] = [permission]
    consumed = sum(
        parent_result.get("usage", {}).get(key, 0) for key in ("input_tokens", "output_tokens")
    )
    remaining = context["execution_budget"]["max_tokens"] - consumed
    if remaining < 1000:
        raise ValueError("Parent remaining token budget cannot reserve child")
    context["execution_budget"] = {
        "max_tokens": 1000,
        "max_cost": 0,
        "max_steps": 3,
        "max_tool_calls": 1,
        "timeout_seconds": 10,
        "max_depth": 0,
        "max_children": 0,
        "concurrency_limit": 1,
    }
    child_definition = {
        **definition,
        "agent_id": "ara.business-reader",
        "tool_ids": [tool],
        "permission_refs": [permission],
        "execution_budget": context["execution_budget"],
        "delegation_policy": {"enabled": False, "max_depth": 0, "max_children": 0},
        "purpose": "Read one authorized canonical business source for the parent analysis.",
    }
    digest = hashlib.sha256(
        json.dumps(child_definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    entry = RegistryEntry(
        subject_type="agent",
        subject_id=child_definition["agent_id"],
        version="1.0.0",
        tenant_id=context["tenant_id"],
        organization_id=context["organization_id"],
        workspace_id=context["workspace_id"],
        payload=child_definition,
        digest=digest,
        state=RegistryState.DRAFT,
        created_by=context["actor_id"],
        correlation_id=context["correlation_id"],
        created_at=datetime.now(UTC),
    )
    run_id = f"run_{uuid4().hex}"
    bundle = copy.deepcopy(parent["context_bundle"])
    bundle.update(
        permission_refs=[permission],
        allowed_tool_ids=[tool],
        execution_budget=context["execution_budget"],
    )
    child = {
        "run_id": run_id,
        "root_run_id": parent["root_run_id"],
        "parent_run_id": parent["run_id"],
        "agent_id": child_definition["agent_id"],
        "agent_version": "1.0.0",
        "capability_id": parent["capability_id"],
        "execution_context": context,
        "context_bundle": bundle,
        "requested_tool_ids": [tool],
        "execution_mode": "TEST",
        "input": {
            "message": "Read canonical source",
            "thread_id": parent["input"]["thread_id"],
            "tool_arguments": {tool: parent["input"].get("tool_arguments", {}).get(tool, {})},
        },
    }
    started = await authority.begin(child, agent=entry)
    try:
        result = await genesis.create_agent_run(
            {
                "agent_definition": child_definition,
                "run_request": child,
                "runtime_authorization": {
                    "run_id": run_id,
                    "registry_digest": digest,
                    "lifecycle_state": started.lifecycle_authorization,
                    "allowed_tool_ids": [tool],
                },
            },
            correlation_id=context["correlation_id"],
            timeout_seconds=context["execution_budget"]["timeout_seconds"],
        )
    except Exception:
        await authority.fail_transport(run_id, code="GENESIS_UNAVAILABLE")
        raise
    await authority.complete(result)
    return result
