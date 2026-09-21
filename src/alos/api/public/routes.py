from typing import Any

from fastapi import APIRouter, Request

from alos import __version__
from alos.api.models import ResearchRequestBody, SystemInfoResponse
from alos.authentication.models import AuthTokenResponse, LoginRequest, RegisterRequest
from alos.context import build_context_projection
from alos.dependencies import (
    CapabilityRegistryDependency,
    ContractCatalogDependency,
    CurrentPrincipalDependency,
    FactoryOrchestratorDependency,
    GenesisClientDependency,
    IntegrationContractValidatorDependency,
    ResearchServiceDependency,
    SkillServiceDependency,
)
from alos.integrations.genesis import GenesisClientError, IntegrationContractError
from alos.observability.correlation import current_correlation_id
from alos.registry_contracts import RegistryAuthorizationError
from alos.research import ResearchCommand, project_domain_access
from alos.security.errors import PlatformError
from alos.skills.models import SkillAssignmentRequest

router = APIRouter(prefix="/api/v1", tags=["system"])

CONTEXT_PROJECTION_SCHEMA = (
    "https://schemas.alos.dev/v1/context/context-projection.schema.json"
)
DOMAIN_ACCESS_SCHEMA = (
    "https://schemas.alos.dev/v1/research/domain-access-response.schema.json"
)


@router.get("/genesis/context-options", tags=["context"])
async def get_context_projection(
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    projection = build_context_projection(
        principal,
        correlation_id=current_correlation_id(),
    )
    return contracts.validate(CONTEXT_PROJECTION_SCHEMA, projection)


@router.get("/research/domain-access", tags=["research"])
async def get_research_domain_access(
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    projection = project_domain_access(
        principal,
        correlation_id=current_correlation_id(),
    )
    return contracts.validate(DOMAIN_ACCESS_SCHEMA, projection)


@router.get("/skills", tags=["skills"])
async def list_skills(
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    return {"skills": skills.list_skills(principal=principal)}


@router.get("/skills/{skill_id}", tags=["skills"])
async def get_skill_detail(
    skill_id: str,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    return skills.get_skill(principal=principal, skill_id=skill_id)


@router.get("/skills/{skill_id}/versions", tags=["skills"])
async def get_skill_versions(
    skill_id: str,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "versions": skills.get_versions(
            principal=principal,
            skill_id=skill_id,
        ),
    }


@router.get("/agents/{agent_id}/skills", tags=["skills"])
async def list_agent_skills(
    agent_id: str,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "skills": skills.list_agent_skills(
            principal=principal,
            agent_id=agent_id,
        ),
    }


@router.post("/skills/assign", tags=["skills"])
async def assign_skill(
    payload: SkillAssignmentRequest,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    response = await skills.assign_skill(
        request=payload,
        principal=principal,
        correlation_id=current_correlation_id(),
    )
    return response.model_dump()


@router.post("/research/requests", tags=["research"])
async def request_research(
    payload: ResearchRequestBody,
    principal: CurrentPrincipalDependency,
    research: ResearchServiceDependency,
) -> dict[str, Any]:
    return await research.request(
        ResearchCommand(
            question=payload.question,
            source_mode=payload.source_mode,
            domain=payload.domain,
        ),
        principal=principal,
        correlation_id=current_correlation_id(),
    )


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
