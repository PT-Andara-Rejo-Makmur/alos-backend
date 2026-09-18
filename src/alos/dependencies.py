"""FastAPI dependency providers."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from alos.authorization import AuthorizationPolicy
from alos.config import Settings, get_settings
from alos.integrations.genesis import GenesisClient, IntegrationContractValidator
from alos.security.errors import PlatformError
from alos.tools.adapters.diagnostic import DiagnosticEchoAdapter
from alos.tools.contracts import JsonSchemaToolContractValidator
from alos.tools.executor.service import ToolExecutor
from alos.tools.policy import DiagnosticPrincipalResolver
from alos.tools.registry import ToolRegistration, ToolRegistry

SettingsDependency = Annotated[Settings, Depends(get_settings)]


async def get_genesis_client(request: Request) -> AsyncIterator[GenesisClient]:
    settings = request.app.state.settings
    client = GenesisClient(
        base_url=settings.GENESIS_BASE_URL,
        internal_token=settings.GENESIS_INTERNAL_TOKEN,
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
    return registry


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
