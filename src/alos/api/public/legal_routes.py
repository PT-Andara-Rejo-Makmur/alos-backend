"""Dedicated canonical schema adapters; resource policy lives in the owner service."""

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes
from alos.domains.legal.records import SPECS

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class LegalPermitCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalPermitCreateRequest"


MODELS[("permits", "create")] = LegalPermitCreateRequest


class LegalPermitUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalPermitUpdateRequest"


MODELS[("permits", "update")] = LegalPermitUpdateRequest


class LegalPermitTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalPermitTransitionRequest"


MODELS[("permits", "transition")] = LegalPermitTransitionRequest


class LegalContractCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalContractCreateRequest"


MODELS[("contracts", "create")] = LegalContractCreateRequest


class LegalContractUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalContractUpdateRequest"


MODELS[("contracts", "update")] = LegalContractUpdateRequest


class LegalContractTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalContractTransitionRequest"


MODELS[("contracts", "transition")] = LegalContractTransitionRequest


class LegalLandDocumentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalLandDocumentCreateRequest"


MODELS[("land_documents", "create")] = LegalLandDocumentCreateRequest


class LegalLandDocumentUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalLandDocumentUpdateRequest"


MODELS[("land_documents", "update")] = LegalLandDocumentUpdateRequest


class LegalLandDocumentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalLandDocumentTransitionRequest"


MODELS[("land_documents", "transition")] = LegalLandDocumentTransitionRequest


class LegalDueDiligenceCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalDueDiligenceCreateRequest"


MODELS[("due_diligences", "create")] = LegalDueDiligenceCreateRequest


class LegalDueDiligenceUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalDueDiligenceUpdateRequest"


MODELS[("due_diligences", "update")] = LegalDueDiligenceUpdateRequest


class LegalDueDiligenceTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalDueDiligenceTransitionRequest"


MODELS[("due_diligences", "transition")] = LegalDueDiligenceTransitionRequest


class LegalDueDiligenceItemCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalDueDiligenceItemCreateRequest"


MODELS[("due_diligence_items", "create")] = LegalDueDiligenceItemCreateRequest


class LegalDueDiligenceItemUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalDueDiligenceItemUpdateRequest"


MODELS[("due_diligence_items", "update")] = LegalDueDiligenceItemUpdateRequest


class LegalDueDiligenceItemTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalDueDiligenceItemTransitionRequest"


MODELS[("due_diligence_items", "transition")] = LegalDueDiligenceItemTransitionRequest


class LegalCaseCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalCaseCreateRequest"


MODELS[("cases", "create")] = LegalCaseCreateRequest


class LegalCaseUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalCaseUpdateRequest"


MODELS[("cases", "update")] = LegalCaseUpdateRequest


class LegalCaseTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalCaseTransitionRequest"


MODELS[("cases", "transition")] = LegalCaseTransitionRequest


class LegalClaimReviewCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalClaimReviewCreateRequest"


MODELS[("claim_reviews", "create")] = LegalClaimReviewCreateRequest


class LegalClaimReviewUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalClaimReviewUpdateRequest"


MODELS[("claim_reviews", "update")] = LegalClaimReviewUpdateRequest


class LegalClaimReviewTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalClaimReviewTransitionRequest"


MODELS[("claim_reviews", "transition")] = LegalClaimReviewTransitionRequest


class LegalExpiryCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalExpiryCreateRequest"


MODELS[("expiries", "create")] = LegalExpiryCreateRequest


class LegalExpiryUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalExpiryUpdateRequest"


MODELS[("expiries", "update")] = LegalExpiryUpdateRequest


class LegalExpiryTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalExpiryTransitionRequest"


MODELS[("expiries", "transition")] = LegalExpiryTransitionRequest


class LegalPrivacyRequestCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalPrivacyRequestCreateRequest"


MODELS[("privacy_requests", "create")] = LegalPrivacyRequestCreateRequest


class LegalPrivacyRequestUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalPrivacyRequestUpdateRequest"


MODELS[("privacy_requests", "update")] = LegalPrivacyRequestUpdateRequest


class LegalPrivacyRequestTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalPrivacyRequestTransitionRequest"


MODELS[("privacy_requests", "transition")] = LegalPrivacyRequestTransitionRequest


class LegalRiskCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalRiskCreateRequest"


MODELS[("risks", "create")] = LegalRiskCreateRequest


class LegalRiskUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalRiskUpdateRequest"


MODELS[("risks", "update")] = LegalRiskUpdateRequest


class LegalRiskTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalRiskTransitionRequest"


MODELS[("risks", "transition")] = LegalRiskTransitionRequest


class LegalControlCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalControlCreateRequest"


MODELS[("controls", "create")] = LegalControlCreateRequest


class LegalControlUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalControlUpdateRequest"


MODELS[("controls", "update")] = LegalControlUpdateRequest


class LegalControlTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalControlTransitionRequest"


MODELS[("controls", "transition")] = LegalControlTransitionRequest


class LegalReviewCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalReviewCreateRequest"


MODELS[("legal_reviews", "create")] = LegalReviewCreateRequest


class LegalReviewUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalReviewUpdateRequest"


MODELS[("legal_reviews", "update")] = LegalReviewUpdateRequest


class LegalReviewTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalReviewTransitionRequest"


MODELS[("legal_reviews", "transition")] = LegalReviewTransitionRequest


class LegalContractRevisionCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/LegalContractRevisionCreateRequest"


MODELS[("contract_revisions", "create")] = LegalContractRevisionCreateRequest


router = register_record_routes("legal", SPECS, MODELS)
