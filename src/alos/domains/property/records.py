"""Curated migration-owned resources and conservative internal lifecycle."""

from dataclasses import replace

from alos.domains.record_repository import MaterialAction, RecordSpec

SPECS = {
    "property_units": RecordSpec(
        "property_units",
        "property_unit_id",
        "PropertyUnit",
        "AVAILABLE",
        {"AVAILABLE": ("ON_HOLD",), "ON_HOLD": ("AVAILABLE",)},
        frozenset(
            ["project_id", "unit_code", "unit_name", "unit_type", "area_land", "area_building"]
        ),
        frozenset(["unit_code", "unit_name", "unit_type", "area_land", "area_building"]),
        False,
    ),
    "project_milestones": RecordSpec(
        "project_milestones",
        "milestone_id",
        "PropertyMilestone",
        "OPEN",
        {"OPEN": ("COMPLETED", "CANCELLED")},
        frozenset(["project_id", "name", "planned_date", "actual_date"]),
        frozenset(["name", "planned_date", "actual_date"]),
        False,
    ),
    "construction_packages": RecordSpec(
        "construction_packages",
        "construction_package_id",
        "PropertyConstructionPackage",
        "PLANNED",
        {
            "PLANNED": ("IN_PROGRESS", "CANCELLED"),
            "IN_PROGRESS": ("ON_HOLD", "COMPLETED", "CANCELLED"),
            "ON_HOLD": ("IN_PROGRESS", "CANCELLED"),
        },
        frozenset(["project_id", "package_code", "name", "contractor_name"]),
        frozenset(["package_code", "name", "contractor_name"]),
        False,
    ),
    "construction_updates": RecordSpec(
        "construction_updates",
        "construction_update_id",
        "PropertyConstructionUpdate",
        "RECORDED",
        {},
        frozenset(["construction_package_id", "update_date", "progress_percent", "summary"]),
        frozenset([]),
        True,
    ),
    "quality_inspections": RecordSpec(
        "quality_inspections",
        "inspection_id",
        "PropertyInspection",
        None,
        {},
        frozenset(["project_id", "inspection_type", "inspection_date", "result", "notes"]),
        frozenset([]),
        True,
    ),
    "quality_ncrs": RecordSpec(
        "quality_ncrs",
        "ncr_id",
        "PropertyNcr",
        "OPEN",
        {"OPEN": ("IN_PROGRESS",), "IN_PROGRESS": ("CLOSED",)},
        frozenset(["inspection_id", "project_id", "title", "severity", "description"]),
        frozenset(["title", "description"]),
        False,
    ),
    "safety_incidents": RecordSpec(
        "safety_incidents",
        "safety_incident_id",
        "PropertySafetyIncident",
        "OPEN",
        {"OPEN": ("IN_PROGRESS",), "IN_PROGRESS": ("CLOSED",)},
        frozenset(["project_id", "incident_date", "severity", "description"]),
        frozenset(["incident_date", "description"]),
        False,
    ),
    "change_orders": RecordSpec(
        "change_orders",
        "change_order_id",
        "PropertyChangeOrder",
        "DRAFT",
        {"DRAFT": ("SUBMITTED", "CANCELLED"), "SUBMITTED": ("CANCELLED",)},
        frozenset(["project_id", "change_number", "description", "amount_delta"]),
        frozenset(["change_number", "description", "amount_delta"]),
        False,
    ),
    "payment_certificates": RecordSpec(
        "payment_certificates",
        "payment_certificate_id",
        "PropertyPaymentCertificate",
        "DRAFT",
        {"DRAFT": ("SUBMITTED", "CANCELLED"), "SUBMITTED": ("CANCELLED",)},
        frozenset(["project_id", "certificate_number", "period", "amount"]),
        frozenset(["certificate_number", "period"]),
        False,
    ),
    "project_handovers": RecordSpec(
        "project_handovers",
        "handover_id",
        "PropertyHandover",
        "PLANNED",
        {"PLANNED": ("COMPLETED", "CANCELLED")},
        frozenset(["project_id", "handover_type", "handover_date", "notes"]),
        frozenset(["handover_type", "handover_date", "notes"]),
        False,
    ),
    "land_pipeline": RecordSpec(
        "land_pipeline",
        "land_pipeline_id",
        "PropertyLandRecord",
        "OPEN",
        {"OPEN": ("ON_HOLD", "CLOSED"), "ON_HOLD": ("OPEN", "CLOSED")},
        frozenset(["location", "area", "owner_name", "estimated_value"]),
        frozenset(["location", "area", "owner_name", "estimated_value"]),
        False,
    ),
}

# These actions require an independent, action-scoped Shared Work approval.
SPECS["property_units"] = replace(
    SPECS["property_units"],
    material_actions=(
        MaterialAction("PROPERTY_UNIT", "RESERVE_UNIT", ("AVAILABLE", "ON_HOLD"), "RESERVED"),
        MaterialAction("PROPERTY_UNIT", "SELL_UNIT", ("RESERVED",), "SOLD"),
    ),
)
SPECS["change_orders"] = replace(
    SPECS["change_orders"],
    material_actions=(
        MaterialAction("PROPERTY_CHANGE_ORDER", "APPROVE_CHANGE_ORDER", ("SUBMITTED",), "APPROVED"),
    ),
)
SPECS["payment_certificates"] = replace(
    SPECS["payment_certificates"],
    material_actions=(
        MaterialAction(
            "PROPERTY_PAYMENT_CERTIFICATE",
            "APPROVE_PAYMENT_CERTIFICATE",
            ("SUBMITTED",),
            "APPROVED",
        ),
    ),
)
