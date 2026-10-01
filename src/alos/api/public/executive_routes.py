"""Canonical Executive projection endpoint."""

from typing import Any, cast

from fastapi import APIRouter, Request

from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.executive.service import ExecutiveProjectionService
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/executive", tags=["executive"])
SCHEMA = "https://schemas.alos.dev/v1/executive/executive-overview-projection.schema.json"


@router.get(
    "/overview",
    openapi_extra={
        "responses": {
            "200": {
                "description": "Governed Strategy read projection",
                "content": {"application/json": {"schema": {"$ref": SCHEMA}}},
            }
        }
    },
)
async def get_overview(
    request: Request, contracts: ContractCatalogDependency, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    projection = await cast(
        ExecutiveProjectionService, request.app.state.executive_service
    ).overview(principal)
    try:
        return contracts.validate(SCHEMA, projection)
    except ValueError as exc:
        raise PlatformError(
            "CONTRACTS_UNAVAILABLE",
            "Executive projection contract is unavailable or invalid.",
            status_code=503,
        ) from exc
