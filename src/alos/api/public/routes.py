from typing import Any

from fastapi import APIRouter, Request

from alos import __version__
from alos.api.models import SystemInfoResponse
from alos.authentication.models import AuthTokenResponse, LoginRequest, RegisterRequest
from alos.dependencies import (
    CapabilityRegistryDependency,
    CurrentPrincipalDependency,
    FactoryOrchestratorDependency,
    GenesisClientDependency,
    IntegrationContractValidatorDependency,
)
from alos.integrations.genesis import GenesisClientError, IntegrationContractError
from alos.observability.correlation import current_correlation_id
from alos.registry_contracts import RegistryAuthorizationError
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/capabilities/{capability_id}", tags=["capabilities"])
async def get_capability_detail(
    capability_id: str,
    principal: CurrentPrincipalDependency,
    capabilities: CapabilityRegistryDependency,
) -> dict[str, Any]:
    """Return the authoritative, authorized CapabilityDetail projection.

    A DRAFT is visible only to its creator; other consumers only receive
    authorized ACTIVE versions. Every other lifecycle state fails closed.
    """

    try:
        return capabilities.detail(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            capability_id=capability_id,
            principal=principal,
        )
    except LookupError as exc:
        raise PlatformError(
            "CAPABILITY_NOT_FOUND",
            "The requested capability version is not available to this principal.",
            status_code=404,
            correlation_id=current_correlation_id(),
        ) from exc
    except RegistryAuthorizationError as exc:
        raise PlatformError(
            "CAPABILITY_NOT_AUTHORIZED",
            "The principal lacks the authority required by this capability version.",
            status_code=403,
            correlation_id=current_correlation_id(),
        ) from exc
    except ValueError as exc:
        raise PlatformError(
            "CAPABILITY_DETAIL_INVALID",
            "Capability detail projection failed contract validation.",
            status_code=500,
            correlation_id=current_correlation_id(),
        ) from exc


@router.post("/genesis/factory/analyze", tags=["factory"])
async def analyze_factory_requirement(
    payload: dict[str, Any],
    principal: CurrentPrincipalDependency,
    orchestrator: FactoryOrchestratorDependency,
) -> dict[str, Any]:
    """Resolve a requirement through Backend authority and GENESIS intelligence."""

    return await orchestrator.analyze(
        payload,
        principal=principal,
        correlation_id=current_correlation_id(),
    )


@router.post("/auth/register", status_code=201)
async def register(request: Request, payload: RegisterRequest) -> dict[str, Any]:
    service = request.app.state.auth_service
    response = service.register(payload.model_dump())
    return {
        "actor_id": response["actor_id"],
        "tenant_id": response["tenant_id"],
        "organization_id": response["organization_id"],
        "workspace_id": response["workspace_id"],
        "email": response["email"],
        "display_name": response["display_name"],
        "permissions": response["permissions"],
        "scopes": response["scopes"],
        "roles": response["roles"],
        "data_scope": response["data_scope"],
        "active": response["active"],
    }


@router.post("/auth/login", response_model=AuthTokenResponse)
async def login(request: Request, payload: LoginRequest) -> AuthTokenResponse:
    service = request.app.state.auth_service
    response = service.login(payload.email, payload.password)
    return AuthTokenResponse(
        access_token=response["access_token"],
        token_type=response["token_type"],
        principal={
            "actor_id": response["actor_id"],
            "tenant_id": response["tenant_id"],
            "organization_id": response["organization_id"],
            "workspace_id": response["workspace_id"],
            "email": response.get("email"),
            "display_name": response.get("display_name"),
            "permissions": response["permissions"],
            "scopes": response["scopes"],
            "roles": response["roles"],
            "data_scope": response["data_scope"],
            "active": response["active"],
        },
    )


@router.get("/auth/whoami")
async def whoami(request: Request) -> dict[str, Any]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PlatformError(
            "MISSING_TOKEN",
            "authorization header is required",
            status_code=401,
        )
    token = auth_header.split(" ", 1)[1].strip()
    principal = request.app.state.auth_service.whoami(token)
    return {
        "actor_id": principal["actor_id"],
        "tenant_id": principal["tenant_id"],
        "organization_id": principal["organization_id"],
        "workspace_id": principal["workspace_id"],
        "email": principal.get("email"),
        "display_name": principal.get("display_name"),
        "permissions": principal["permissions"],
        "scopes": principal["scopes"],
        "roles": principal["roles"],
        "data_scope": principal["data_scope"],
        "active": principal["active"],
    }


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
