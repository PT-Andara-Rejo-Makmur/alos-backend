from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Request

from alos.authentication.internal import verify_internal_token
from alos.dependencies import DiagnosticPrincipalResolverDependency, ToolExecutorDependency
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


@router.post("/tool-requests")
async def execute_tool_request(
    payload: dict[str, Any],
    executor: ToolExecutorDependency,
    principal_resolver: DiagnosticPrincipalResolverDependency,
    _request: Request,
) -> dict[str, Any]:
    """Internal-only governed ToolRequest to ToolResult boundary."""

    context = payload.get("execution_context")
    principal = principal_resolver.resolve(context) if isinstance(context, Mapping) else None
    outcome = await executor.execute(
        payload,
        principal=principal,
        transport_correlation_id=current_correlation_id(),
    )
    return outcome.result
