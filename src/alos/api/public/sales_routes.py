"""Named schema-validated HTTP adapters; canonical schemas own every field."""

from typing import cast

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes, response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.property.references import PropertyUnitReferences
from alos.domains.sales.records import SPECS
from alos.domains.sales.service import SalesService
from alos.security.errors import PlatformError

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class SalesCustomerCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesCustomerCreateRequest"


MODELS[("customers", "create")] = SalesCustomerCreateRequest


class SalesCustomerUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesCustomerUpdateRequest"


MODELS[("customers", "update")] = SalesCustomerUpdateRequest


class SalesCustomerTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesCustomerTransitionRequest"


MODELS[("customers", "transition")] = SalesCustomerTransitionRequest


class SalesLeadCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesLeadCreateRequest"


MODELS[("leads", "create")] = SalesLeadCreateRequest


class SalesLeadUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesLeadUpdateRequest"


MODELS[("leads", "update")] = SalesLeadUpdateRequest


class SalesLeadTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesLeadTransitionRequest"


MODELS[("leads", "transition")] = SalesLeadTransitionRequest


class SalesOpportunityCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesOpportunityCreateRequest"


MODELS[("opportunities", "create")] = SalesOpportunityCreateRequest


class SalesOpportunityUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesOpportunityUpdateRequest"


MODELS[("opportunities", "update")] = SalesOpportunityUpdateRequest


class SalesOpportunityTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesOpportunityTransitionRequest"


MODELS[("opportunities", "transition")] = SalesOpportunityTransitionRequest


class SalesSiteVisitCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesSiteVisitCreateRequest"


MODELS[("site_visits", "create")] = SalesSiteVisitCreateRequest


class SalesSiteVisitUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesSiteVisitUpdateRequest"


MODELS[("site_visits", "update")] = SalesSiteVisitUpdateRequest


class SalesSiteVisitTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesSiteVisitTransitionRequest"


MODELS[("site_visits", "transition")] = SalesSiteVisitTransitionRequest


class SalesBookingCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesBookingCreateRequest"


MODELS[("bookings", "create")] = SalesBookingCreateRequest


class SalesBookingUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesBookingUpdateRequest"


MODELS[("bookings", "update")] = SalesBookingUpdateRequest


class SalesBookingTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesBookingTransitionRequest"


MODELS[("bookings", "transition")] = SalesBookingTransitionRequest


class SalesClosingCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesClosingCreateRequest"


MODELS[("closings", "create")] = SalesClosingCreateRequest


class SalesClosingUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesClosingUpdateRequest"


MODELS[("closings", "update")] = SalesClosingUpdateRequest


class SalesClosingTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesClosingTransitionRequest"


MODELS[("closings", "transition")] = SalesClosingTransitionRequest


class SalesFollowupCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesFollowupCreateRequest"


MODELS[("customer_followups", "create")] = SalesFollowupCreateRequest


class SalesFollowupUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesFollowupUpdateRequest"


MODELS[("customer_followups", "update")] = SalesFollowupUpdateRequest


class SalesFollowupTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesFollowupTransitionRequest"


MODELS[("customer_followups", "transition")] = SalesFollowupTransitionRequest


class SalesComplaintCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesComplaintCreateRequest"


MODELS[("customer_complaints", "create")] = SalesComplaintCreateRequest


class SalesComplaintUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesComplaintUpdateRequest"


MODELS[("customer_complaints", "update")] = SalesComplaintUpdateRequest


class SalesComplaintTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesComplaintTransitionRequest"


MODELS[("customer_complaints", "transition")] = SalesComplaintTransitionRequest


class SalesPricingCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesPricingCreateRequest"


MODELS[("pricings", "create")] = SalesPricingCreateRequest


class SalesPricingUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesPricingUpdateRequest"


MODELS[("pricings", "update")] = SalesPricingUpdateRequest


class SalesPricingTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesPricingTransitionRequest"


MODELS[("pricings", "transition")] = SalesPricingTransitionRequest


class SalesPricingItemCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesPricingItemCreateRequest"


MODELS[("pricing_items", "create")] = SalesPricingItemCreateRequest


class SalesPricingItemUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesPricingItemUpdateRequest"


MODELS[("pricing_items", "update")] = SalesPricingItemUpdateRequest


class SalesCollateralCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesCollateralCreateRequest"


MODELS[("collaterals", "create")] = SalesCollateralCreateRequest


class SalesCollateralUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesCollateralUpdateRequest"


MODELS[("collaterals", "update")] = SalesCollateralUpdateRequest


class SalesCollateralTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesCollateralTransitionRequest"


MODELS[("collaterals", "transition")] = SalesCollateralTransitionRequest

class SalesFinancingCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesFinancingCreateRequest"


class SalesFinancingUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesFinancingUpdateRequest"


class SalesFinancingTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesFinancingTransitionRequest"


MODELS[("financing_contexts", "create")] = SalesFinancingCreateRequest
MODELS[("financing_contexts", "update")] = SalesFinancingUpdateRequest
MODELS[("financing_contexts", "transition")] = SalesFinancingTransitionRequest
router = register_record_routes("sales", SPECS, MODELS)


class SalesOpportunityPipelineRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesOpportunityPipelineRequest"


@router.post("/opportunities/{identity}/pipeline")
async def advance_pipeline(
    identity: str,
    payload: SalesOpportunityPipelineRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> JSONResponse:
    values = payload.validated(contracts)
    try:
        data = await cast(SalesService, request.app.state.sales_service).advance_pipeline(
            principal, identity, values["stage"]
        )
    except SQLAlchemyError as exc:
        raise PlatformError(
            "BUSINESS_SOURCE_ERROR", "Pipeline could not be persisted.", status_code=503
        ) from exc
    return response(
        contracts,
        "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesOpportunityProjection",
        data,
    )


@router.get("/property-units")
async def visible_units(
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    try:
        data = await cast(
            PropertyUnitReferences, request.app.state.property_unit_references
        ).listing(principal, limit, offset)
    except SQLAlchemyError as exc:
        raise PlatformError(
            "BUSINESS_SOURCE_ERROR", "Property source could not be read.", status_code=503
        ) from exc
    return response(
        contracts,
        "https://schemas.alos.dev/v1/sales/sales-contracts.schema.json#/$defs/SalesUnitReferenceListProjection",
        data,
    )
