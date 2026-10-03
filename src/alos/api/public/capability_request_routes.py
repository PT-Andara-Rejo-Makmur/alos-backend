"""Business need forms are independent of model and tool configuration."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from sqlalchemy import update

from alos.api.public.record_routes import CanonicalRecordRequest, response
from alos.dependencies import (
    ContractCatalogDependency,
    CurrentPrincipalDependency,
    FactoryOrchestratorDependency,
    GenesisClientDependency,
)
from alos.evidence.registry.service import resolve_registry_result
from alos.factory.business_requests import CapabilityBusinessRequests
from alos.factory.review_context import review_invocation
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import revalidate
from alos.security.errors import PlatformError

router = APIRouter(
    prefix="/api/v1/business/capability-requests", tags=["capability-business-requests"]
)
BASE = "https://schemas.alos.dev/v1/business/business-contracts.schema.json#/$defs/"


class CapabilityBusinessRequestCreate(CanonicalRecordRequest):
    schema_uri = BASE + "CapabilityBusinessRequestCreate"


class CapabilityBusinessResolutionRequest(CanonicalRecordRequest):
    schema_uri = BASE + "CapabilityBusinessResolutionRequest"


def service(request: Request) -> CapabilityBusinessRequests:
    return CapabilityBusinessRequests(request.app.state.process_service.repository)


@router.post("/{request_id}/review")
async def review_request(
    request_id: str,
    payload: CapabilityBusinessResolutionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> Any:
    from alos.api.public.routes import request_advisory_review

    owner = service(request)
    owner.authorize(principal, resolve=True)
    reason = payload.validated(contracts)["reason"]
    current = await owner.get(principal, request_id)
    result = current["factory_result"] or {}
    draft = result.get("agent_draft")
    if current["resolution_state"] != "RESOLVED" or draft is None:
        raise PlatformError(
            "CAPABILITY_REVIEW_UNAVAILABLE",
            "Draft agent belum tersedia untuk tinjauan.",
            status_code=409,
        )
    entry = request.app.state.agent_registry.get(
        tenant_id=principal.tenant_id,
        workspace_id=principal.workspace_id,
        subject_id=draft["agent_id"],
        version=draft["version"],
    )
    correlation = current_correlation_id()
    evidence = await resolve_registry_result(
        request.app.state.evidence_registry.register(
            {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "correlation_id": correlation,
                "scope_refs": entry.payload["scope_refs"],
                "evidence_id": "evidence_" + uuid4().hex,
                "source_id": "registry." + entry.subject_id,
                "source_version": entry.version,
                "uri": "urn:alos:registry:" + entry.subject_id + ":" + entry.version,
                "captured_at": datetime.now(UTC).isoformat(),
                "content_hash": "sha256:" + entry.digest,
                "data_classification": "INTERNAL",
                "instruction_authority": False,
                "validation_status": "VALID",
                "excerpt": (
                    "Snapshot definisi draft yang tersimpan dalam registry Backend; "
                    "bukan bukti bahwa pengujian lulus."
                ),
                "metadata": {
                    "review_subject": {
                        "subject_id": entry.subject_id,
                        "subject_version": entry.version,
                    },
                    "business_request_id": request_id,
                },
            }
        )
    )
    invocation = review_invocation(entry, principal, correlation, evidence)
    package = await request_advisory_review(invocation, request, principal, contracts, genesis)
    async with owner.repository.factory() as session, session.begin():
        await revalidate(owner.repository, session, principal)
        table = await owner.repository.table(session, "core", "capability_request_resolutions")
        await session.execute(
            update(table)
            .where(table.c.request_id == request_id)
            .values(review_id=package["identity"]["review_id"])
        )
        await owner.event(
            session,
            principal,
            request_id,
            "reviewed",
            reason,
            {"review_id": package["identity"]["review_id"]},
        )
    return package


@router.post("", status_code=201)
async def create_request(
    payload: CapabilityBusinessRequestCreate,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    return response(
        contracts,
        BASE + "CapabilityBusinessRequest",
        await service(request).create(principal, payload.validated(contracts)),
        status=201,
    )


@router.get("")
async def list_requests(
    request: Request, principal: CurrentPrincipalDependency, contracts: ContractCatalogDependency
) -> Any:
    return response(
        contracts,
        BASE + "CapabilityBusinessRequestOverview",
        await service(request).list(principal),
    )


@router.get("/{request_id}")
async def get_request(
    request_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    return response(
        contracts,
        BASE + "CapabilityBusinessRequest",
        await service(request).get(principal, request_id),
    )


@router.post("/{request_id}/resolve")
async def resolve_request(
    request_id: str,
    payload: CapabilityBusinessResolutionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    factory: FactoryOrchestratorDependency,
) -> Any:
    return response(
        contracts,
        BASE + "CapabilityBusinessRequest",
        await service(request).resolve(
            principal, request_id, payload.validated(contracts)["reason"], factory
        ),
    )
