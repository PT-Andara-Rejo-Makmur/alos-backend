"""Named schema-validated HTTP adapters; canonical schemas own every field."""

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes
from alos.domains.marketing.records import SPECS

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class MarketingCampaignCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingCampaignCreateRequest"


MODELS[("campaigns", "create")] = MarketingCampaignCreateRequest


class MarketingCampaignUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingCampaignUpdateRequest"


MODELS[("campaigns", "update")] = MarketingCampaignUpdateRequest


class MarketingCampaignTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingCampaignTransitionRequest"


MODELS[("campaigns", "transition")] = MarketingCampaignTransitionRequest


class MarketingChannelCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingChannelCreateRequest"


MODELS[("channels", "create")] = MarketingChannelCreateRequest


class MarketingChannelUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingChannelUpdateRequest"


MODELS[("channels", "update")] = MarketingChannelUpdateRequest


class MarketingChannelTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingChannelTransitionRequest"


MODELS[("channels", "transition")] = MarketingChannelTransitionRequest


class MarketingAttributionCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingAttributionCreateRequest"


MODELS[("attributions", "create")] = MarketingAttributionCreateRequest


class MarketingAttributionUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingAttributionUpdateRequest"


MODELS[("attributions", "update")] = MarketingAttributionUpdateRequest


class MarketingContentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingContentCreateRequest"


MODELS[("contents", "create")] = MarketingContentCreateRequest


class MarketingContentUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingContentUpdateRequest"


MODELS[("contents", "update")] = MarketingContentUpdateRequest


class MarketingContentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/marketing/marketing-contracts.schema.json#/$defs/MarketingContentTransitionRequest"


MODELS[("contents", "transition")] = MarketingContentTransitionRequest

router = register_record_routes("marketing", SPECS, MODELS)
