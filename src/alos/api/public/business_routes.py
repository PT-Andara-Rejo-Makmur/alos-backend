"""Authoritative business performance shared by Web, ARA and Executive."""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request

from alos.api.public.record_routes import CanonicalRecordRequest, response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.business_relationships import relationships
from alos.domains.strategy.models import LifecycleState
from alos.domains.strategy.projections import project
from alos.domains.strategy.read_model import overview, target_detail
from alos.notifications.business import BusinessNotifications
from alos.projections.business import business_summary
from alos.projections.business_analytics import (
    AnalyticsPeriodError,
    Granularity,
    business_analytics,
)
from alos.projections.project_records import project_records
from alos.projections.work_queue import work_queue
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/business", tags=["business-performance"])
BASE = "https://schemas.alos.dev/v1/business/business-contracts.schema.json"


class MetricBindingRequest(CanonicalRecordRequest):
    schema_uri = BASE + "#/$defs/MetricBindingRequest"


class MetricCalculationRequest(CanonicalRecordRequest):
    schema_uri = BASE + "#/$defs/MetricCalculationRequest"


@router.get("/work-queue")
async def business_work_queue(
    request: Request, principal: CurrentPrincipalDependency, contracts: ContractCatalogDependency
) -> Any:
    result = await work_queue(
        request.app.state.shared_work_service, request.app.state.process_service, principal
    )
    return response(contracts, BASE + "#/$defs/BusinessWorkQueue", result)


@router.get("/notifications")
async def business_notifications(
    request: Request, principal: CurrentPrincipalDependency, contracts: ContractCatalogDependency
) -> Any:
    result = await BusinessNotifications(request.app.state.process_service.repository).list(
        principal
    )
    return response(contracts, BASE + "#/$defs/BusinessNotificationOverview", result)


@router.get("/projects/{project_id}/records")
async def project_business_records(
    project_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await project_records(
        request.app.state.process_service.repository,
        request.app.state.shared_work_service,
        principal,
        project_id,
    )
    return response(contracts, BASE + "#/$defs/BusinessProjectRecordOverview", result)


@router.get("/relationships/{record_type}/{identity}")
async def record_relationships(
    record_type: str,
    identity: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await relationships(
        request.app.state.process_service.repository, principal, record_type, identity
    )
    return response(contracts, BASE + "#/$defs/BusinessRelationshipOverview", result)


@router.post("/targets/{target_id}/source-binding", status_code=201)
async def bind_metric(
    target_id: str,
    payload: MetricBindingRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await request.app.state.business_metric_service.bind(
        principal,
        target_id,
        payload.validated(contracts),
    )
    return response(contracts, BASE + "#/$defs/MetricBindingProjection", result, status=201)


@router.post("/targets/{target_id}/calculate", status_code=201)
async def calculate_metric(
    target_id: str,
    payload: MetricCalculationRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    values = payload.validated(contracts)
    result = await request.app.state.strategy_service.derive_actual(
        principal,
        target_id,
        values["target_version"],
        values["request_id"],
    )
    return response(
        contracts,
        "https://schemas.alos.dev/v1/strategy/metric-observation.schema.json",
        project(result),
        status=201,
    )


@router.get("/{domain}/summary")
async def summary(
    domain: Literal["sales", "property", "finance", "legal", "hr", "it"],
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    repository = request.app.state.process_service.repository
    return response(
        contracts,
        BASE + "#/$defs/BusinessSummary",
        await business_summary(repository, principal, domain),
    )


@router.get("/executive/analytics")
async def executive_analytics(
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    granularity: Granularity,
) -> Any:
    repository = request.app.state.process_service.repository
    try:
        result = await business_analytics(
            repository,
            principal,
            "executive",
            date_from,
            date_to,
            granularity,
            executive=True,
            shared_work=request.app.state.shared_work_service,
            strategy_service=request.app.state.strategy_service,
        )
    except AnalyticsPeriodError as exc:
        raise PlatformError(
            "BUSINESS_ANALYTICS_PERIOD_INVALID",
            "The requested analytics period is invalid or unsupported.",
            status_code=422,
        ) from exc
    return response(contracts, BASE + "#/$defs/BusinessAnalyticsProjection", result)


@router.get("/{domain}/analytics")
async def analytics(
    domain: Literal["sales", "property", "finance", "legal", "hr", "it"],
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    granularity: Granularity,
) -> Any:
    repository = request.app.state.process_service.repository
    try:
        result = await business_analytics(
            repository,
            principal,
            domain,
            date_from,
            date_to,
            granularity,
            shared_work=request.app.state.shared_work_service,
        )
    except AnalyticsPeriodError as exc:
        raise PlatformError(
            "BUSINESS_ANALYTICS_PERIOD_INVALID",
            "The requested analytics period is invalid or unsupported.",
            status_code=422,
        ) from exc
    return response(contracts, BASE + "#/$defs/BusinessAnalyticsProjection", result)


@router.get("/executive/performance")
async def executive_performance(
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    repository = request.app.state.process_service.repository
    summaries = [
        await business_summary(repository, principal, domain, executive=True)
        for domain in ("sales", "property", "finance", "legal", "hr", "it")
    ]
    strategy = request.app.state.strategy_service
    targets = await strategy.list_targets(principal)
    details = [
        await target_detail(strategy, principal, target)
        for target in targets
        if target.lifecycle_state is LifecycleState.ACTIVE
    ]
    queue = await request.app.state.process_service.queue(principal)
    decisions = [
        item
        for item in queue["items"]
        if any(step["can_act"] and step["kind"] == "DECISION" for step in item["steps"])
    ]
    acknowledgements = [
        item
        for item in queue["items"]
        if any(step["can_act"] and step["kind"] == "ACKNOWLEDGEMENT" for step in item["steps"])
    ]
    attention_codes = {
        "overdue_receivables",
        "open_ncr",
        "open_incidents",
        "open_security_findings",
        "open_risks",
    }
    attention = [
        {"domain": summary["domain"], **metric}
        for summary in summaries
        for metric in summary["metrics"]
        if metric["code"] in attention_codes
        and metric["available"]
        and metric["value"] is not None
        and float(metric["value"]) > 0
    ]
    return response(
        contracts,
        BASE,
        {
            "domains": summaries,
            "strategy": await overview(strategy, principal),
            "target_details": details,
            "decisions": decisions,
            "attention": attention,
            "acknowledgements": acknowledgements,
        },
    )
