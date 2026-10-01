"""Curated migration-owned resources and conservative internal lifecycle."""

from typing import Any

from alos.domains.record_repository import RecordSpec

PIPELINE_EDGES = {"Lead": "Qualified", "Qualified": "Survey", "Survey": "Booking"}


def pipeline_projection(row: dict[str, Any]) -> dict[str, Any]:
    next_step = PIPELINE_EDGES.get(row["stage"]) if row["status"] == "OPEN" else None
    return {"allowed_pipeline_stages": [next_step] if next_step else []}


SPECS = {
    "customers": RecordSpec(
        "customers",
        "customer_id",
        "SalesCustomer",
        "ACTIVE",
        {"ACTIVE": ("INACTIVE",), "INACTIVE": ("ACTIVE",)},
        frozenset(["customer_code", "customer_type", "name", "email", "phone"]),
        frozenset(["customer_code", "customer_type", "name", "email", "phone"]),
        False,
    ),
    "leads": RecordSpec(
        "leads",
        "lead_id",
        "SalesLead",
        "NEW",
        {
            "NEW": ("FOLLOW_UP", "QUALIFIED", "INACTIVE"),
            "FOLLOW_UP": ("QUALIFIED", "INACTIVE"),
            "QUALIFIED": ("INACTIVE",),
        },
        frozenset(["customer_id", "source", "interest"]),
        frozenset(["source", "interest"]),
        False,
    ),
    "opportunities": RecordSpec(
        "opportunities",
        "opportunity_id",
        "SalesOpportunity",
        "OPEN",
        {"OPEN": ("LOST", "CANCELLED")},
        frozenset(["customer_id", "lead_id", "name", "stage", "estimated_value", "probability"]),
        frozenset(["name", "estimated_value", "probability"]),
        False,
        pipeline_projection,
    ),
    "site_visits": RecordSpec(
        "site_visits",
        "site_visit_id",
        "SalesSiteVisit",
        "SCHEDULED",
        {"SCHEDULED": ("COMPLETED", "CANCELLED")},
        frozenset(["customer_id", "property_unit_id", "scheduled_at", "notes"]),
        frozenset(["scheduled_at", "notes"]),
        False,
    ),
    "bookings": RecordSpec(
        "bookings",
        "booking_id",
        "SalesBooking",
        "PENDING",
        {"PENDING": ("CANCELLED",)},
        frozenset(["customer_id", "property_unit_id", "booking_date", "amount"]),
        frozenset(["booking_date"]),
        False,
    ),
    "closings": RecordSpec(
        "closings",
        "closing_id",
        "SalesClosing",
        "OPEN",
        {"OPEN": ("CANCELLED",)},
        frozenset(["booking_id", "customer_id", "property_unit_id", "closing_date", "amount"]),
        frozenset(["closing_date"]),
        False,
    ),
    "customer_followups": RecordSpec(
        "customer_followups",
        "followup_id",
        "SalesFollowup",
        "OPEN",
        {"OPEN": ("COMPLETED", "CANCELLED")},
        frozenset(["customer_id", "opportunity_id", "followup_type", "scheduled_at", "notes"]),
        frozenset(["followup_type", "scheduled_at", "notes"]),
        False,
    ),
    "customer_complaints": RecordSpec(
        "customer_complaints",
        "complaint_id",
        "SalesComplaint",
        "OPEN",
        {"OPEN": ("IN_PROGRESS", "CLOSED"), "IN_PROGRESS": ("CLOSED",)},
        frozenset(["customer_id", "category", "description"]),
        frozenset(["category", "description"]),
        False,
    ),
    "pricings": RecordSpec(
        "pricings",
        "pricing_id",
        "SalesPricing",
        "DRAFT",
        {"DRAFT": (), "ACTIVE": ("INACTIVE",)},
        frozenset(["name", "effective_from", "effective_to"]),
        frozenset(["name", "effective_from", "effective_to"]),
        False,
    ),
    "pricing_items": RecordSpec(
        "pricing_items",
        "pricing_item_id",
        "SalesPricingItem",
        None,
        {},
        frozenset(["pricing_id", "property_unit_id", "price", "currency"]),
        frozenset(["price"]),
        False,
    ),
    "collaterals": RecordSpec(
        "collaterals",
        "collateral_id",
        "SalesCollateral",
        "ACTIVE",
        {"ACTIVE": ("INACTIVE",), "INACTIVE": ("ACTIVE",)},
        frozenset(["name", "collateral_type", "campaign_id", "document_id"]),
        frozenset(["name", "collateral_type"]),
        False,
    ),
}
