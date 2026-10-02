"""Public ARA accepts minimal user intent; authority always comes from Principal."""

from typing import Any

from fastapi import APIRouter, Request

from alos.ara.orchestration import AraOrchestrator
from alos.ara.repository import AraRepository
from alos.contracts import ContractValidationError
from alos.dependencies import (
    ContractCatalogDependency,
    CurrentPrincipalDependency,
    GenesisClientDependency,
    get_tool_registry,
)
from alos.factory import FactoryOrchestrator
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/ara", tags=["ara"])


def service(request: Request, contracts: Any, genesis: Any) -> AraOrchestrator:
    settings = request.app.state.settings
    return AraOrchestrator(
        repository=AraRepository(request.app.state.database.session_factory),
        contracts=contracts,
        authority=request.app.state.agent_run_authority,
        genesis=genesis,
        registry=get_tool_registry(request),
        audit=request.app.state.identity_audit,
        test_enabled=settings.APP_ENV in {"development", "test"} and settings.ENABLE_TEST_TOOLS,
        memory=request.app.state.memory_service,
        evidence_registry=request.app.state.evidence_registry,
        factory=FactoryOrchestrator(
            contracts=contracts,
            genesis=genesis,
            capabilities=request.app.state.factory_capability_registry,
            agents=request.app.state.factory_agent_registry,
        ),
        agents=request.app.state.factory_agent_registry,
    )


def validate(orchestrator: AraOrchestrator, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return orchestrator.validate(name, payload)
    except ContractValidationError as exc:
        raise PlatformError(
            "ARA_CONTRACT_INVALID", "Data ARA tidak valid.", status_code=422
        ) from exc


@router.get("/authority")
async def authority(
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    orchestrator = service(request, contracts, genesis)
    result = orchestrator.authority_projection(principal)
    try:
        released = False
        policy_ref = "ara.production"
        if not orchestrator.test_enabled:
            try:
                policy_ref = orchestrator.production_agent(principal).payload["model_policy_ref"]
                released = True
            except ValueError:
                pass
        await genesis.health(correlation_id=current_correlation_id())
        readiness = await genesis.provider_readiness(
            correlation_id=current_correlation_id(), policy_ref=policy_ref
        )
        result["production_provider_connected"] = readiness.get("status") == "CONNECTED" and all(
            readiness.get(key) is True
            for key in ("configured", "reachable", "authenticated", "model_selected")
        )
        result["service_available"] = orchestrator.test_enabled or (
            released and result["production_provider_connected"]
        )
    except Exception:
        result["service_available"] = False
    return result


@router.post("/threads", status_code=201)
async def create_thread(
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    orchestrator = service(request, contracts, genesis)
    data = validate(orchestrator, "ara-thread-create-request", payload)
    result = await orchestrator.repository.create(principal, data.get("title", "Percakapan ARA"))
    await orchestrator.record(
        principal, result["thread_id"], "ara.thread_created", current_correlation_id(), "CREATED"
    )
    return validate(orchestrator, "ara-thread-projection", result)


@router.get("/threads")
async def threads(
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> list[dict[str, Any]]:
    return await service(request, contracts, genesis).repository.list_threads(principal)


@router.get("/threads/{thread_id}")
async def thread(
    thread_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    return await service(request, contracts, genesis).repository.get(principal, thread_id)


@router.get("/threads/{thread_id}/messages")
async def messages(
    thread_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> list[dict[str, Any]]:
    return await service(request, contracts, genesis).repository.messages(principal, thread_id)


@router.post("/threads/{thread_id}/messages")
async def send(
    thread_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    orchestrator = service(request, contracts, genesis)
    validate(orchestrator, "ara-message-request", payload)
    return await orchestrator.send(principal, thread_id, payload, current_correlation_id())


@router.get("/threads/{thread_id}/runs/{run_id}")
async def run(
    thread_id: str,
    run_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    return await service(request, contracts, genesis).repository.run(principal, thread_id, run_id)


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel(
    thread_id: str,
    run_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    orchestrator = service(request, contracts, genesis)
    row = await orchestrator.repository.run(principal, thread_id, run_id)
    await orchestrator.authority.request_cancel(
        run_id, actor_id=principal.actor_id, correlation_id=current_correlation_id()
    )
    await orchestrator.record(
        principal,
        run_id,
        "ara.cancellation_requested",
        current_correlation_id(),
        "CANCEL_REQUESTED",
    )
    return {**row, "status": "CANCEL_REQUESTED"}
