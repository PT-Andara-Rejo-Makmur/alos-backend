from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from alos.agents.lifecycle import RunAuthorityError
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
    if isinstance(context, Mapping):
        try:
            run = await _request.app.state.agent_run_authority.get(str(payload.get("run_id", "")))
        except RunAuthorityError:
            run = None
        if run is not None and all(
            context.get(key) == expected
            for key, expected in {
                "tenant_id": run.tenant_id,
                "organization_id": run.organization_id,
                "workspace_id": run.workspace_id,
                "actor_id": run.actor_id,
                "correlation_id": run.correlation_id,
            }.items()
        ):
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
    outcome = await executor.execute(
        payload,
        principal=principal,
        transport_correlation_id=current_correlation_id(),
    )
    return outcome.result
