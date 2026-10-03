"""Named schema-validated HTTP adapters; canonical schemas own every field."""

from typing import Any

from fastapi import Request

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes, response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.property.records import SPECS

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class PropertyUnitCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyUnitCreateRequest"


MODELS[("property_units", "create")] = PropertyUnitCreateRequest


class PropertyUnitUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyUnitUpdateRequest"


MODELS[("property_units", "update")] = PropertyUnitUpdateRequest


class PropertyUnitTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyUnitTransitionRequest"


MODELS[("property_units", "transition")] = PropertyUnitTransitionRequest


class PropertyMilestoneCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyMilestoneCreateRequest"


MODELS[("project_milestones", "create")] = PropertyMilestoneCreateRequest


class PropertyMilestoneUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyMilestoneUpdateRequest"


MODELS[("project_milestones", "update")] = PropertyMilestoneUpdateRequest


class PropertyMilestoneTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyMilestoneTransitionRequest"


MODELS[("project_milestones", "transition")] = PropertyMilestoneTransitionRequest


class PropertyConstructionPackageCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyConstructionPackageCreateRequest"


MODELS[("construction_packages", "create")] = PropertyConstructionPackageCreateRequest


class PropertyConstructionPackageUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyConstructionPackageUpdateRequest"


MODELS[("construction_packages", "update")] = PropertyConstructionPackageUpdateRequest


class PropertyConstructionPackageTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyConstructionPackageTransitionRequest"


MODELS[("construction_packages", "transition")] = PropertyConstructionPackageTransitionRequest


class PropertyConstructionUpdateCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyConstructionUpdateCreateRequest"


MODELS[("construction_updates", "create")] = PropertyConstructionUpdateCreateRequest


class PropertyConstructionUpdateUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyConstructionUpdateUpdateRequest"


MODELS[("construction_updates", "update")] = PropertyConstructionUpdateUpdateRequest


class PropertyConstructionUpdateTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyConstructionUpdateTransitionRequest"


MODELS[("construction_updates", "transition")] = PropertyConstructionUpdateTransitionRequest


class PropertyInspectionCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyInspectionCreateRequest"


MODELS[("quality_inspections", "create")] = PropertyInspectionCreateRequest


class PropertyInspectionUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyInspectionUpdateRequest"


MODELS[("quality_inspections", "update")] = PropertyInspectionUpdateRequest


class PropertyNcrCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyNcrCreateRequest"


MODELS[("quality_ncrs", "create")] = PropertyNcrCreateRequest


class PropertyNcrUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyNcrUpdateRequest"


MODELS[("quality_ncrs", "update")] = PropertyNcrUpdateRequest


class PropertyNcrTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyNcrTransitionRequest"


MODELS[("quality_ncrs", "transition")] = PropertyNcrTransitionRequest


class PropertySafetyIncidentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertySafetyIncidentCreateRequest"


MODELS[("safety_incidents", "create")] = PropertySafetyIncidentCreateRequest


class PropertySafetyIncidentUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertySafetyIncidentUpdateRequest"


MODELS[("safety_incidents", "update")] = PropertySafetyIncidentUpdateRequest


class PropertySafetyIncidentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertySafetyIncidentTransitionRequest"


MODELS[("safety_incidents", "transition")] = PropertySafetyIncidentTransitionRequest


class PropertyChangeOrderCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyChangeOrderCreateRequest"


MODELS[("change_orders", "create")] = PropertyChangeOrderCreateRequest


class PropertyChangeOrderUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyChangeOrderUpdateRequest"


MODELS[("change_orders", "update")] = PropertyChangeOrderUpdateRequest


class PropertyChangeOrderTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyChangeOrderTransitionRequest"


MODELS[("change_orders", "transition")] = PropertyChangeOrderTransitionRequest


class PropertyPaymentCertificateCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyPaymentCertificateCreateRequest"


MODELS[("payment_certificates", "create")] = PropertyPaymentCertificateCreateRequest


class PropertyPaymentCertificateUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyPaymentCertificateUpdateRequest"


MODELS[("payment_certificates", "update")] = PropertyPaymentCertificateUpdateRequest


class PropertyPaymentCertificateTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyPaymentCertificateTransitionRequest"


MODELS[("payment_certificates", "transition")] = PropertyPaymentCertificateTransitionRequest


class PropertyHandoverCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyHandoverCreateRequest"


MODELS[("project_handovers", "create")] = PropertyHandoverCreateRequest


class PropertyHandoverUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyHandoverUpdateRequest"


MODELS[("project_handovers", "update")] = PropertyHandoverUpdateRequest


class PropertyHandoverTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyHandoverTransitionRequest"


MODELS[("project_handovers", "transition")] = PropertyHandoverTransitionRequest


class PropertyLandRecordCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyLandRecordCreateRequest"


MODELS[("land_pipeline", "create")] = PropertyLandRecordCreateRequest


class PropertyLandRecordUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyLandRecordUpdateRequest"


MODELS[("land_pipeline", "update")] = PropertyLandRecordUpdateRequest


class PropertyLandRecordTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyLandRecordTransitionRequest"


MODELS[("land_pipeline", "transition")] = PropertyLandRecordTransitionRequest

router = register_record_routes("property", SPECS, MODELS)


class PropertyChangeOrderImplementationRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyChangeOrderImplementationRequest"


@router.post("/change-orders/{identity}/implement")
async def implement_change_order(
    identity: str,
    payload: PropertyChangeOrderImplementationRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await request.app.state.property_service.implement_change_order(
        principal, identity, payload.validated(contracts)["reason"]
    )
    return response(
        contracts,
        "https://schemas.alos.dev/v1/property/property-contracts.schema.json#/$defs/PropertyChangeOrderProjection",
        result,
    )
