"""Audited migration-owned resources and conservative internal record lifecycle."""

from alos.domains.record_repository import RecordSpec
from alos.identity import Principal


def transition_visible(status: str, principal: Principal) -> bool:
    operational = (
        bool(principal.roles & {"DIVISION_LEAD", "DIVISION_MEMBER"})
        and "legal.write" in principal.permissions
    )
    return operational and (
        status not in {"CLOSED", "REVIEWED"} or "DIVISION_LEAD" in principal.roles
    )


SPECS = {
    "permits": RecordSpec(
        "permits",
        "permit_id",
        "LegalPermit",
        "RECORDED",
        {"RECORDED": ("ARCHIVED",), "ARCHIVED": ()},
        frozenset(["expires_at", "issued_at", "permit_number", "permit_type", "subject"]),
        frozenset(["expires_at", "issued_at", "permit_number", "permit_type", "subject"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "contracts": RecordSpec(
        "contracts",
        "contract_id",
        "LegalContract",
        "DRAFT",
        {"DRAFT": ("IN_REVIEW",), "IN_REVIEW": ("DRAFT",)},
        frozenset(
            [
                "contract_number",
                "contract_type",
                "counterparty_name",
                "document_id",
                "end_date",
                "start_date",
            ]
        ),
        frozenset(
            ["contract_number", "contract_type", "counterparty_name", "end_date", "start_date"]
        ),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "land_documents": RecordSpec(
        "land_documents",
        "land_document_id",
        "LegalLandDocument",
        "RECORDED",
        {"RECORDED": ("ARCHIVED",), "ARCHIVED": ()},
        frozenset(
            [
                "document_id",
                "document_number",
                "document_type",
                "expires_at",
                "holder_name",
                "issued_at",
                "property_ref",
            ]
        ),
        frozenset(["document_number", "document_type", "expires_at", "holder_name", "issued_at"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "due_diligences": RecordSpec(
        "due_diligences",
        "due_diligence_id",
        "LegalDueDiligence",
        "OPEN",
        {"OPEN": ("IN_REVIEW",), "IN_REVIEW": ("OPEN",)},
        frozenset(["subject_id", "subject_type", "title"]),
        frozenset(["title"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "due_diligence_items": RecordSpec(
        "due_diligence_items",
        "due_diligence_item_id",
        "LegalDueDiligenceItem",
        "OPEN",
        {"OPEN": ("IN_PROGRESS",), "IN_PROGRESS": ("COMPLETED",), "COMPLETED": ()},
        frozenset(["description", "due_diligence_id", "finding", "item_type"]),
        frozenset(["description", "finding", "item_type"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "cases": RecordSpec(
        "cases",
        "case_id",
        "LegalCase",
        "OPEN",
        {"OPEN": ("IN_REVIEW",), "IN_REVIEW": ("OPEN",)},
        frozenset(["case_number", "case_type", "description", "title"]),
        frozenset(["case_number", "case_type", "description", "title"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "claim_reviews": RecordSpec(
        "claim_reviews",
        "claim_review_id",
        "LegalClaimReview",
        "OPEN",
        {"OPEN": ("IN_REVIEW",), "IN_REVIEW": ("REVIEWED",), "REVIEWED": ()},
        frozenset(["assessment", "claim_amount", "claimant_name", "subject_id", "subject_type"]),
        frozenset(["assessment", "claim_amount", "claimant_name"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "expiries": RecordSpec(
        "expiries",
        "expiry_id",
        "LegalExpiry",
        "OPEN",
        {"OPEN": ("ACKNOWLEDGED",), "ACKNOWLEDGED": ()},
        frozenset(["expires_at", "reminder_days", "subject_id", "subject_type"]),
        frozenset(["expires_at", "reminder_days"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "privacy_requests": RecordSpec(
        "privacy_requests",
        "privacy_request_id",
        "LegalPrivacyRequest",
        "OPEN",
        {"OPEN": ("IN_PROGRESS",), "IN_PROGRESS": ()},
        frozenset(["description", "due_at", "request_type", "requester_ref"]),
        frozenset(["description", "due_at", "request_type", "requester_ref"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "risks": RecordSpec(
        "risks",
        "risk_id",
        "LegalRisk",
        "OPEN",
        {"OPEN": ("MITIGATING",), "MITIGATING": ("REVIEWED",), "REVIEWED": ()},
        frozenset(["description", "impact", "likelihood", "rating", "title"]),
        frozenset(["description", "impact", "likelihood", "rating", "title"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
    "controls": RecordSpec(
        "controls",
        "control_id",
        "LegalControl",
        "ACTIVE",
        {"ACTIVE": ("INACTIVE",), "INACTIVE": ("ACTIVE",)},
        frozenset(["control_code", "description", "frequency", "title"]),
        frozenset(["control_code", "description", "frequency", "title"]),
        False,
        transition_authorized=transition_visible,
        status_field="status",
    ),
}

# Internal recorded capabilities identified by the canonical closure audit.
SPECS["legal_reviews"] = RecordSpec(
    "legal_reviews",
    "legal_review_id",
    "LegalReview",
    "OPEN",
    {"OPEN": ("IN_REVIEW",), "IN_REVIEW": ("OPEN", "REVIEWED")},
    frozenset(["contract_id", "title", "review_summary", "assessment"]),
    frozenset(["title", "review_summary", "assessment"]),
    immutable=False,
    transition_authorized=transition_visible,
)
SPECS["contract_revisions"] = RecordSpec(
    "contract_revisions",
    "contract_revision_id",
    "LegalContractRevision",
    "RECORDED",
    {},
    frozenset(
        [
            "contract_id",
            "document_id",
            "document_version",
            "revision_number",
            "summary",
            "recorded_on",
        ]
    ),
    frozenset([]),
    immutable=True,
)
