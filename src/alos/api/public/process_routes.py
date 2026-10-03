"""Minimal business intents; all routing and permissions are resolved by Backend."""

from typing import Any

from fastapi import APIRouter, Request

from alos.api.public.record_routes import CanonicalRecordRequest, response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.processes.direction import request_direction
from alos.processes.service import ProcessService
from alos.processes.tasks import create_task

BASE = "https://schemas.alos.dev/v1/process/process-contracts.schema.json"
router = APIRouter(prefix="/api/v1/processes", tags=["business-processes"])


class ProcessStartRequest(CanonicalRecordRequest):
    schema_uri = BASE + "#/$defs/BusinessProcessStartRequest"


class ProcessActionRequest(CanonicalRecordRequest):
    schema_uri = BASE + "#/$defs/BusinessProcessActionRequest"


class ProcessPolicyRequest(CanonicalRecordRequest):
    schema_uri = BASE + "#/$defs/BusinessProcessPolicyRequest"


class ProcessRevisionRequest(CanonicalRecordRequest):
    schema_uri = BASE + "#/$defs/BusinessProcessRevisionRequest"


@router.post("/{process_id}/steps/{step_id}/task", status_code=201)
async def connect_task(
    process_id: str,
    step_id: str,
    payload: ProcessRevisionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await create_task(
        service(request),
        request.app.state.shared_work_service,
        principal,
        process_id,
        step_id,
        payload.validated(contracts)["reason"],
    )
    return response(
        contracts,
        "https://schemas.alos.dev/v1/shared-work/shared-work.schema.json#/$defs/TaskProjection",
        result,
        status=201,
    )


def service(request: Request) -> ProcessService:
    return request.app.state.process_service  # type: ignore[no-any-return]


@router.post("/policies")
async def configure_policy(
    payload: ProcessPolicyRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await service(request).configure(principal, payload.validated(contracts))
    return response(contracts, BASE + "#/$defs/BusinessProcessPolicyProjection", result)


@router.get("/work-queue")
async def work_queue(
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    return response(contracts, BASE, await service(request).queue(principal))


@router.post("", status_code=201)
async def start_process(
    payload: ProcessStartRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await service(request).start(principal, payload.validated(contracts))
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result, status=201)


@router.get("/{process_id}")
async def process_detail(
    process_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await service(request).detail(principal, process_id)
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result)


@router.get("/subjects/{business_type}/{subject_id}")
async def subject_process(
    business_type: str,
    subject_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await service(request).for_subject(principal, business_type, subject_id)
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result)


@router.post("/{process_id}/steps/{step_id}/actions")
async def process_action(
    process_id: str,
    step_id: str,
    payload: ProcessActionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await service(request).act(
        principal, process_id, step_id, payload.validated(contracts)
    )
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result)


@router.post("/{process_id}/resubmit")
async def resubmit_process(
    process_id: str,
    payload: ProcessRevisionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    values = payload.validated(contracts)
    result = await service(request).revise(principal, process_id, values["reason"])
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result)


@router.post("/{process_id}/request-direction")
async def director_direction(
    process_id: str,
    payload: ProcessRevisionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await request_direction(
        service(request), principal, process_id, payload.validated(contracts)["reason"]
    )
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result)


@router.post("/{process_id}/cancel")
async def cancel_process(
    process_id: str,
    payload: ProcessRevisionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    values = payload.validated(contracts)
    result = await service(request).revise(principal, process_id, values["reason"], cancel=True)
    return response(contracts, BASE + "#/$defs/BusinessProcessProjection", result)
