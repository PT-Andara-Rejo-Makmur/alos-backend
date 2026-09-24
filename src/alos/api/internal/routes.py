from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from alos.agents.lifecycle import AuthoritativeRunStatus, RunAuthorityError
from alos.authentication.internal import verify_internal_token
from alos.dependencies import DiagnosticPrincipalResolverDependency, ToolExecutorDependency
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(verify_internal_token)],
)


@router.get("/health", include_in_schema=False)
async def internal_health() -> dict[str, str]:
    """Real liveness signal for trusted service-to-service connectivity."""
    return {"status": "ok", "boundary": "alos-backend"}


@router.get("/agent-runs/{run_id}/cancellation")
async def get_agent_run_cancellation(run_id: str, request: Request) -> dict[str, str]:
    record = await request.app.state.agent_run_authority.get(run_id)
    return {
        "run_id": record.run_id,
        "status": record.status.value,
        "cancellation_state": record.cancellation_state,
    }


@router.get("/integration/agent-runs", include_in_schema=False)
async def find_integration_agent_run(
    correlation_id: str,
    request: Request,
) -> dict[str, str]:
    settings = request.app.state.settings
    if not settings.ENABLE_TEST_TOOLS or settings.APP_ENV not in {"development", "test"}:
        raise HTTPException(status_code=404, detail="integration inspection is disabled")
    matches = [
        record
        for record in await request.app.state.agent_run_authority.list_runs()
        if record.correlation_id == correlation_id
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="integration run was not found")
    record = max(matches, key=lambda item: item.created_at)
    return {"run_id": record.run_id, "status": record.status.value}


@router.post("/tool-requests")
async def execute_tool_request(
    payload: dict[str, Any],
    executor: ToolExecutorDependency,
    principal_resolver: DiagnosticPrincipalResolverDependency,
    _request: Request,
) -> dict[str, Any]:
    """Internal-only governed ToolRequest to ToolResult boundary."""

    context = payload.get("execution_context")
    principal = None
    authorized_tool_ids: frozenset[str] | None = frozenset()
    if isinstance(context, Mapping):
        try:
            run = await _request.app.state.agent_run_authority.get(str(payload.get("run_id", "")))
        except RunAuthorityError:
            run = None
        run_context = run.request.get("execution_context", {}) if run is not None else {}
        identity_matches = run is not None and all(
            context.get(key) == expected
            for key, expected in {
                "tenant_id": run.tenant_id,
                "organization_id": run.organization_id,
                "workspace_id": run.workspace_id,
                "actor_id": run.actor_id,
                "correlation_id": run.correlation_id,
            }.items()
        )
        authority_matches = isinstance(run_context, dict) and all(
            context.get(key) == run_context.get(key)
            for key in ("authority_context", "data_classification", "execution_budget")
            if key in context or key in run_context
        )
        permission_refs = frozenset(str(item) for item in context.get("permission_refs", []))
        scope_refs = frozenset(str(item) for item in context.get("scope_refs", []))
        run_permissions = frozenset(
            str(item) for item in run_context.get("permission_refs", [])
        ) if isinstance(run_context, dict) else frozenset()
        run_scopes = frozenset(
            str(item) for item in run_context.get("scope_refs", [])
        ) if isinstance(run_context, dict) else frozenset()
        declared_tool_ids = frozenset(
            str(item) for item in context.get("allowed_tool_ids", [])
        )
        run_tool_ids = frozenset(run.authorized_tool_ids) if run is not None else frozenset()
        if (
            run is not None
            and run.status is AuthoritativeRunStatus.RUNNING
            and identity_matches
            and authority_matches
            and permission_refs.issubset(run_permissions)
            and scope_refs.issubset(run_scopes)
            and declared_tool_ids.issubset(run_tool_ids)
        ):
            authorized_tool_ids = run_tool_ids
            run_context = run.request.get("execution_context", {})
            if isinstance(run_context, dict):
                principal = Principal(
                    actor_id=run.actor_id,
                    tenant_id=run.tenant_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    permissions=frozenset(
                        str(item) for item in run_context.get("permission_refs", [])
                    ),
                    scopes=frozenset(str(item) for item in run_context.get("scope_refs", [])),
                    roles=frozenset(),
                )
    if principal is None and _request.app.state.settings.APP_ENV == "test":
        principal = principal_resolver.resolve(context) if isinstance(context, Mapping) else None
        authorized_tool_ids = None
    outcome = await executor.execute(
        payload,
        principal=principal,
        transport_correlation_id=current_correlation_id(),
        authorized_tool_ids=authorized_tool_ids,
    )
    return outcome.result
