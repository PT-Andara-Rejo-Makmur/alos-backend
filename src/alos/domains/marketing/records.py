"""Curated migration-owned resources and conservative internal lifecycle."""

from alos.domains.record_repository import RecordSpec

SPECS = {
    "campaigns": RecordSpec(
        "campaigns",
        "campaign_id",
        "MarketingCampaign",
        "PLANNED",
        {"PLANNED": ("ACTIVE", "CANCELLED"), "ACTIVE": ("COMPLETED", "CANCELLED")},
        frozenset(["name", "campaign_type", "start_date", "end_date", "budget"]),
        frozenset(["name", "campaign_type", "start_date", "end_date", "budget"]),
        False,
    ),
    "channels": RecordSpec(
        "channels",
        "channel_id",
        "MarketingChannel",
        "ACTIVE",
        {"ACTIVE": ("INACTIVE",), "INACTIVE": ("ACTIVE",)},
        frozenset(["name", "channel_type"]),
        frozenset(["name", "channel_type"]),
        False,
    ),
    "attributions": RecordSpec(
        "attributions",
        "attribution_id",
        "MarketingAttribution",
        None,
        {},
        frozenset(
            ["customer_id", "lead_id", "campaign_id", "channel_id", "touch_type", "occurred_at"]
        ),
        frozenset([]),
        True,
    ),
    "contents": RecordSpec(
        "contents",
        "content_id",
        "MarketingContent",
        "DRAFT",
        {"DRAFT": ("PUBLISHED",), "PUBLISHED": ("ARCHIVED",)},
        frozenset(["campaign_id", "title", "content_type"]),
        frozenset(["title", "content_type"]),
        False,
    ),
}
