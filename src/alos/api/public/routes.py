from typing import Any

from fastapi import APIRouter, Request

from alos import __version__
from alos.api.models import SystemInfoResponse
from alos.dependencies import GenesisClientDependency, IntegrationContractValidatorDependency
from alos.integrations.genesis import GenesisClientError, IntegrationContractError
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/system/info", response_model=SystemInfoResponse)
async def system_info(request: Request) -> SystemInfoResponse:
    settings = request.app.state.settings
    return SystemInfoResponse(
        service="alos-backend",
        version=__version__,
        environment=settings.APP_ENV,
    )


@router.get("/system/integration")
async def integration_diagnostic(
    genesis_client: GenesisClientDependency,
    contract_validator: IntegrationContractValidatorDependency,
) -> dict[str, Any]:
    correlation_id = current_correlation_id()
    try:
        genesis_response = await genesis_client.diagnostic(correlation_id=correlation_id)
        contract_validator.validate_genesis_response(genesis_response)
        if genesis_response["correlation_id"] != correlation_id:
            raise IntegrationContractError(
                path="correlation_id",
                reason="GENESIS must return the correlation_id sent by Backend.",
            )
    except GenesisClientError as exc:
        status_code = 503 if exc.code in {"GENESIS_TIMEOUT", "GENESIS_UNAVAILABLE"} else 502
        raise PlatformError(
            exc.code,
            exc.message,
            status_code=status_code,
            retryable=exc.retryable,
            correlation_id=correlation_id,
        ) from exc
    except IntegrationContractError as exc:
        raise PlatformError(
            "GENESIS_INVALID_RESPONSE",
            "GENESIS response does not satisfy the canonical contract.",
            status_code=502,
            details={"path": exc.path, "reason": exc.reason},
            correlation_id=correlation_id,
        ) from exc

    response: dict[str, Any] = {
        "correlation_id": correlation_id,
        "status": "connected",
        "backend": {
            "service": "alos-backend",
            "status": "reachable",
            "authority": "ALOS_BACKEND",
        },
        "genesis": genesis_response,
    }
    try:
        contract_validator.validate_public_response(response)
    except IntegrationContractError as exc:
        raise PlatformError(
            "INTEGRATION_RESPONSE_INVALID",
            "Backend integration response does not satisfy the canonical contract.",
            status_code=500,
            details={"path": exc.path, "reason": exc.reason},
            correlation_id=correlation_id,
        ) from exc
    return response
