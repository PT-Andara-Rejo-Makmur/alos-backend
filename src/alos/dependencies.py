"""FastAPI dependency providers."""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request

from alos.agents.registry import AgentRegistry
from alos.agents.runtime import AuthoritativeRuntimeOrchestrator
from alos.audit import InMemoryAuditRepository
from alos.authorization import AuthorizationEnforcer, AuthorizationPolicy
from alos.capabilities.registry import CapabilityRegistry
from alos.config import Settings, get_settings
from alos.context.bundle import ContextBundleBuilder
from alos.contracts import CanonicalContractCatalog
from alos.factory import FactoryOrchestrator
from alos.identity import DataScope, Principal
from alos.integrations import ExternalRetrievalService
from alos.integrations.genesis import GenesisClient, IntegrationContractValidator
from alos.permissions import PermissionRegistry
from alos.releases import GovernedAgentLifecycle, PersistentReleaseAuthority
from alos.research import ResearchService
from alos.security.errors import PlatformError
from alos.skills.registry import SkillRegistry
from alos.skills.service import SkillService
from alos.tools.adapters.diagnostic import DiagnosticEchoAdapter
from alos.tools.contracts import JsonSchemaToolContractValidator
from alos.tools.executor.service import ToolExecutor
from alos.tools.external_research import ExternalResearchToolAdapter
from alos.tools.policy import DiagnosticPrincipalResolver
from alos.tools.registry import ToolRegistration, ToolRegistry

SettingsDependency = Annotated[Settings, Depends(get_settings)]


async def get_genesis_client(request: Request) -> AsyncIterator[GenesisClient]:
    settings = request.app.state.settings
    client = GenesisClient(
        base_url=settings.GENESIS_BASE_URL,
        internal_token=settings.GENESIS_INTERNAL_TOKEN,
        client=getattr(request.app.state, "genesis_http_client", None),
    )
    try:
        yield client
    finally:
        await client.close()


def get_integration_contract_validator(request: Request) -> IntegrationContractValidator:
    contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
    if contracts_path is None:
        raise PlatformError(
            "CONTRACTS_NOT_CONFIGURED",
            "ALOS_CONTRACTS_PATH is required for integration contract validation.",
            status_code=503,
            retryable=False,
        )
    try:
        return IntegrationContractValidator(contracts_path)
    except (OSError, ValueError) as exc:
        raise PlatformError(
            "CONTRACTS_UNAVAILABLE",
            "Canonical integration contracts could not be loaded.",
            status_code=503,
            retryable=False,
            details={"reason": str(exc)},
        ) from exc


GenesisClientDependency = Annotated[GenesisClient, Depends(get_genesis_client)]
IntegrationContractValidatorDependency = Annotated[
    IntegrationContractValidator,
    Depends(get_integration_contract_validator),
]

ExternalRetrievalDependency = Annotated[
    ExternalRetrievalService,
    Depends(lambda request: request.app.state.external_retrieval_service),
]


async def get_current_principal(request: Request) -> Principal:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PlatformError(
            "MISSING_TOKEN",
            "authorization header is required",
            status_code=401,
        )
    token = auth_header.split(" ", 1)[1].strip()
    payload = await request.app.state.auth_service.whoami(token)
    active_access = payload.get("active_workspace")
    if not isinstance(active_access, dict):
        raise PlatformError(
            "ACTIVE_WORKSPACE_REQUIRED",
            "an active workspace must be selected",
            status_code=403,
        )
    workspace = active_access["workspace"]
    actor = payload["actor"]
    return Principal(
        actor_id=str(actor["actor_id"]),
        tenant_id=str(actor["tenant_id"]),
        organization_id=str(actor["organization_id"]),
        workspace_id=str(workspace["workspace_id"]),
        permissions=frozenset(str(item) for item in active_access["permission_refs"]),
        scopes=frozenset(str(item) for item in active_access["scope_refs"]),
        roles=frozenset(str(item) for item in active_access["role_refs"]),
        data_scope=DataScope(str(active_access["data_scope"])),
        division_id=(str(workspace["division_code"]) if workspace.get("division_code") else None),
        project_id=None,
        active=bool(actor["active"] and active_access["active"]),
    )


def get_authorization_enforcer(request: Request) -> AuthorizationEnforcer:
    audit = getattr(request.app.state, "authz_audit", None)
    if audit is None:
        audit = InMemoryAuditRepository()
        request.app.state.authz_audit = audit
    return AuthorizationEnforcer(
        policy=AuthorizationPolicy(),
        permissions=PermissionRegistry(),
        audit=audit,
    )


def get_context_bundle_builder(request: Request) -> ContextBundleBuilder:
    return ContextBundleBuilder()


CurrentPrincipalDependency = Annotated[Principal, Depends(get_current_principal)]
AuthorizationEnforcerDependency = Annotated[
    AuthorizationEnforcer, Depends(get_authorization_enforcer)
]
ContextBundleBuilderDependency = Annotated[
    ContextBundleBuilder, Depends(get_context_bundle_builder)
]


def get_release_authority(request: Request) -> PersistentReleaseAuthority:
    return cast(PersistentReleaseAuthority, request.app.state.release_authority)


ReleaseAuthorityDependency = Annotated[
    PersistentReleaseAuthority, Depends(get_release_authority)
]


def get_agent_lifecycle(request: Request) -> GovernedAgentLifecycle:
    lifecycle = getattr(request.app.state, "agent_lifecycle", None)
    if not isinstance(lifecycle, GovernedAgentLifecycle):
        raise PlatformError(
            "AGENT_LIFECYCLE_UNAVAILABLE",
            "Governed agent lifecycle is unavailable.",
            status_code=503,
        )
    return lifecycle


AgentLifecycleDependency = Annotated[
    GovernedAgentLifecycle, Depends(get_agent_lifecycle)
]


def get_contract_catalog(request: Request) -> CanonicalContractCatalog:
    contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
    if contracts_path is None:
        raise PlatformError(
            "CONTRACTS_NOT_CONFIGURED",
            "ALOS_CONTRACTS_PATH is required for governed API contracts.",
            status_code=503,
        )
    try:
        return CanonicalContractCatalog(contracts_path)
    except (OSError, ValueError) as exc:
        raise PlatformError(
            "CONTRACTS_UNAVAILABLE",
            "Canonical contracts could not be loaded.",
            status_code=503,
            details={"reason": str(exc)},
        ) from exc


ContractCatalogDependency = Annotated[
    CanonicalContractCatalog,
    Depends(get_contract_catalog),
]


def get_research_service(
    request: Request,
    contracts: ContractCatalogDependency,
    genesis_client: GenesisClientDependency,
) -> ResearchService:
    return ResearchService(
        contracts=contracts,
        genesis=genesis_client,
        audit=request.app.state.research_audit,
    )


ResearchServiceDependency = Annotated[ResearchService, Depends(get_research_service)]


def get_runtime_orchestrator(
    request: Request,
    genesis_client: GenesisClientDependency,
) -> AuthoritativeRuntimeOrchestrator:
    return AuthoritativeRuntimeOrchestrator(
        authority=request.app.state.agent_run_authority,
        genesis=genesis_client,
    )


RuntimeOrchestratorDependency = Annotated[
    AuthoritativeRuntimeOrchestrator, Depends(get_runtime_orchestrator)
]


async def get_factory_orchestrator(
    request: Request,
    genesis_client: GenesisClientDependency,
) -> FactoryOrchestrator:
    contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
    if contracts_path is None:
        raise PlatformError(
            "CONTRACTS_NOT_CONFIGURED",
            "ALOS_CONTRACTS_PATH is required for Factory orchestration.",
            status_code=503,
            retryable=False,
        )
    if request.app.state.factory_contracts is None:
        raise PlatformError(
            "CONTRACTS_UNAVAILABLE",
            "Canonical Factory contracts could not be loaded.",
            status_code=503,
            retryable=False,
        )
    return FactoryOrchestrator(
        contracts=request.app.state.factory_contracts,
        genesis=genesis_client,
        capabilities=request.app.state.factory_capability_registry,
        agents=request.app.state.factory_agent_registry,
    )


FactoryOrchestratorDependency = Annotated[
    FactoryOrchestrator,
    Depends(get_factory_orchestrator),
]


def get_capability_registry(request: Request) -> CapabilityRegistry:
    """Shared lazy capability authority state; identical wiring to the Factory orchestrator."""

    contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
    if contracts_path is None:
        raise PlatformError(
            "CONTRACTS_NOT_CONFIGURED",
            "ALOS_CONTRACTS_PATH is required for capability governance.",
            status_code=503,
            retryable=False,
        )
    registry = request.app.state.factory_capability_registry
    if not isinstance(registry, CapabilityRegistry):
        raise PlatformError(
            "CAPABILITY_REGISTRY_UNAVAILABLE",
            "The authoritative capability registry is not available.",
            status_code=503,
        )
    return registry


CapabilityRegistryDependency = Annotated[
    CapabilityRegistry,
    Depends(get_capability_registry),
]


def get_tool_contract_validator(request: Request) -> JsonSchemaToolContractValidator:
    contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
    if contracts_path is None:
        raise PlatformError(
            "CONTRACTS_NOT_CONFIGURED",
            "ALOS_CONTRACTS_PATH is required for ToolRequest validation.",
            status_code=503,
        )
    try:
        return JsonSchemaToolContractValidator(contracts_path)
    except (OSError, ValueError) as exc:
        raise PlatformError(
            "CONTRACTS_UNAVAILABLE",
            "Canonical tool contracts could not be loaded.",
            status_code=503,
            details={"reason": str(exc)},
        ) from exc


def get_tool_registry(request: Request) -> ToolRegistry:
    settings = request.app.state.settings
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="diagnostic.echo",
            required_permission="tools.diagnostic.execute",
            required_scopes=frozenset({"scope.diagnostic"}),
            adapter=DiagnosticEchoAdapter(),
            allowlisted=settings.ENABLE_TEST_TOOLS,
            production_enabled=False,
            timeout_seconds=2.0,
        )
    )
    registry.register(
        ToolRegistration(
            tool_id="external.research",
            required_permission="research.external.read",
            required_scopes=frozenset({"scope.sources.external_read"}),
            adapter=ExternalResearchToolAdapter(),
            allowlisted=True,
            production_enabled=True,
            timeout_seconds=15.0,
        )
    )
    return registry


async def get_skill_service(request: Request) -> SkillService:
    contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
    if contracts_path is None:
        raise PlatformError(
            "CONTRACTS_NOT_CONFIGURED",
            "ALOS_CONTRACTS_PATH is required for skill governance.",
            status_code=503,
        )
    existing = getattr(request.app.state, "skill_service", None)
    if isinstance(existing, SkillService):
        return existing
    contracts = request.app.state.factory_contracts
    if not isinstance(contracts, CanonicalContractCatalog):
        raise PlatformError(
            "CONTRACTS_UNAVAILABLE",
            "Canonical skill contracts could not be loaded.",
            status_code=503,
        )
    registry = getattr(request.app.state, "skill_registry", None)
    if not isinstance(registry, SkillRegistry):
        raise PlatformError(
            "SKILL_REGISTRY_UNAVAILABLE",
            "The authoritative Skill Registry is not available.",
            status_code=503,
        )
    audit = getattr(request.app.state, "skill_audit", None)
    agents = getattr(request.app.state, "agent_registry", None)
    if not isinstance(agents, AgentRegistry):
        raise PlatformError(
            "AGENT_REGISTRY_UNAVAILABLE",
            "The shared authoritative Agent Registry is not available.",
            status_code=503,
        )
    service = SkillService(registry=registry, agents=agents, audit=audit)
    request.app.state.skill_service = service
    return service


SkillServiceDependency = Annotated[SkillService, Depends(get_skill_service)]


def get_tool_executor(
    request: Request,
    contract_validator: Annotated[
        JsonSchemaToolContractValidator, Depends(get_tool_contract_validator)
    ],
    registry: Annotated[ToolRegistry, Depends(get_tool_registry)],
) -> ToolExecutor:
    return ToolExecutor(
        contract_validator=contract_validator,
        authorization=AuthorizationPolicy(),
        registry=registry,
        audit_sink=request.app.state.tool_audit_sink,
        idempotency_store=request.app.state.tool_idempotency_store,
        production=request.app.state.settings.APP_ENV == "production",
    )


ToolExecutorDependency = Annotated[ToolExecutor, Depends(get_tool_executor)]
DiagnosticPrincipalResolverDependency = Annotated[
    DiagnosticPrincipalResolver,
    Depends(DiagnosticPrincipalResolver),
]
