"""Backend-owned orchestration for canonical GENESIS runtime invocations."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from alos.agents.lifecycle import (
    AgentRunAuthority,
    AuthoritativeRunRecord,
    AuthoritativeRunStatus,
    AuthoritativeStepStatus,
)
from alos.identity import Principal
from alos.integrations.genesis import GenesisClient
from alos.observability.correlation import current_correlation_id
from alos.registry import RegistryEntry, RegistryState
from alos.registry_contracts import RegistryAuthorityView


class AuthoritativeRuntimeOrchestrator:
    def __init__(self, *, authority: AgentRunAuthority, genesis: GenesisClient) -> None:
        self._authority = authority
        self._genesis = genesis

    async def execute(
        self,
        payload: dict[str, Any],
        *,
        principal: Principal,
        agent: RegistryEntry,
        test_mode_allowed: bool,
    ) -> AuthoritativeRunRecord:
        RegistryAuthorityView.from_entry(agent).authorize(principal)
        if agent.state is not RegistryState.ACTIVE:
            raise ValueError("Agent lifecycle is not ACTIVE")
        execution_mode = str(payload.get("execution_mode", "NORMAL"))
        if execution_mode == "TEST" and not test_mode_allowed:
            raise ValueError("TEST execution is not enabled")
        capability_id = str(payload.get("capability_id", ""))
        if capability_id not in agent.payload.get("capability_ids", []):
            raise ValueError("Requested capability is outside the exact Agent definition")
        requested_tools = tuple(str(item) for item in payload.get("requested_tool_ids", []))
        requested_scopes = frozenset(
            str(item) for item in payload.get("scope_refs", principal.scopes)
        )
        if not requested_scopes.issubset(principal.scopes):
            raise ValueError("Requested scope expands caller authority")
        correlation_id = current_correlation_id()
        run_id = f"run_{uuid4().hex}"
        run_request: dict[str, Any] = {
            "run_id": run_id,
            "root_run_id": run_id,
            "agent_id": agent.subject_id,
            "agent_version": agent.version,
            "capability_id": capability_id,
            "execution_context": {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "actor_id": principal.actor_id,
                "authority_context": {
                    "role": sorted(principal.roles)[0] if principal.roles else "authorized_actor",
                    "role_refs": sorted(principal.roles),
                    "authority_level": "REQUESTER",
                },
                "permission_refs": sorted(principal.permissions),
                "allowed_tool_ids": sorted(
                    set(requested_tools).intersection(agent.payload.get("tool_ids", []))
                ),
                "scope_refs": sorted(requested_scopes),
                "data_classification": str(payload.get("data_classification", "INTERNAL")),
                "correlation_id": correlation_id,
                "execution_budget": dict(payload.get("execution_budget", {})),
            },
            "input": dict(payload.get("input", {})),
            "requested_tool_ids": list(requested_tools),
            "execution_mode": execution_mode,
        }
        started = await self._authority.begin(run_request, agent=agent)
        invocation = {
            "agent_definition": agent.payload,
            "run_request": started.request,
            "runtime_authorization": {
                "run_id": started.run_id,
                "registry_digest": started.registry_digest,
                "lifecycle_state": started.lifecycle_authorization,
                "allowed_tool_ids": list(started.authorized_tool_ids),
            },
        }
        result = await self._genesis.create_agent_run(invocation, correlation_id=correlation_id)
        await self._persist_tool_steps(started.run_id, result, correlation_id=correlation_id)
        return await self._authority.complete(result)

    async def _persist_tool_steps(
        self,
        run_id: str,
        result: dict[str, Any],
        *,
        correlation_id: str,
    ) -> None:
        """Persist canonical tool observations before the run becomes terminal."""
        current = await self._authority.get(run_id)
        if current.status is AuthoritativeRunStatus.CANCEL_REQUESTED:
            return
        for sequence, item in enumerate(result.get("tool_results", []), start=1):
            if not isinstance(item, dict):
                continue
            tool_result = dict(item)
            status = str(tool_result.get("status", "FAILED"))
            step = await self._authority.begin_step(
                run_id,
                step_type="tool",
                tool_id=str(tool_result.get("tool_id") or "backend.tool"),
                sequence=sequence,
                correlation_id=correlation_id,
            )
            await self._authority.update_step_status(
                step.step_id,
                status=(
                    AuthoritativeStepStatus.SUCCEEDED
                    if status in {"SUCCESS", "COMPLETED"}
                    else AuthoritativeStepStatus.FAILED
                ),
                output_metadata=tool_result,
                error_code=(
                    str(tool_result.get("error", {}).get("code", status))
                    if status not in {"SUCCESS", "COMPLETED"}
                    and isinstance(tool_result.get("error"), dict)
                    else None
                ),
            )
