"""Add Sales and Marketing workspace domain persistence.

Revision ID: 0017_sales_marketing
Revises: 0016_legal
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_sales_marketing"
down_revision = "0016_legal"
branch_labels = None
depends_on = None


def _scope(schema="core"):
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            index=True,
        ),
    ]


def _dates():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "sales"'))
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "marketing"'))

    op.create_table(
        "customers",
        *_scope(),
        sa.Column("customer_id", sa.String(128), primary_key=True),
        sa.Column("customer_code", sa.String(128), nullable=False),
        sa.Column("customer_type", sa.String(64), nullable=False, server_default="INDIVIDUAL"),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "customer_code", name="uq_sales_customer_code"
        ),
        schema="sales",
    )
    op.create_table(
        "leads",
        *_scope(),
        sa.Column("lead_id", sa.String(128), primary_key=True),
        sa.Column("customer_id", sa.String(128), nullable=True, index=True),
        sa.Column("source", sa.String(128), nullable=True),
        sa.Column("interest", sa.String(500), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="NEW"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "opportunities",
        *_scope(),
        sa.Column("opportunity_id", sa.String(128), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(128),
            sa.ForeignKey("sales.customers.customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("lead_id", sa.String(128), nullable=True, index=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False, index=True),
        sa.Column("estimated_value", sa.Numeric(20, 2), nullable=True),
        sa.Column("probability", sa.Numeric(8, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "site_visits",
        *_scope(),
        sa.Column("site_visit_id", sa.String(128), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(128),
            sa.ForeignKey("sales.customers.customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("property_unit_id", sa.String(128), nullable=True, index=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="SCHEDULED"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "bookings",
        *_scope(),
        sa.Column("booking_id", sa.String(128), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(128),
            sa.ForeignKey("sales.customers.customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("property_unit_id", sa.String(128), nullable=False, index=True),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PENDING"),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "closings",
        *_scope(),
        sa.Column("closing_id", sa.String(128), primary_key=True),
        sa.Column(
            "booking_id",
            sa.String(128),
            sa.ForeignKey("sales.bookings.booking_id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "customer_id",
            sa.String(128),
            sa.ForeignKey("sales.customers.customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("property_unit_id", sa.String(128), nullable=False, index=True),
        sa.Column("closing_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "customer_followups",
        *_scope(),
        sa.Column("followup_id", sa.String(128), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(128),
            sa.ForeignKey("sales.customers.customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "opportunity_id",
            sa.String(128),
            sa.ForeignKey("sales.opportunities.opportunity_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("followup_type", sa.String(128), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "customer_complaints",
        *_scope(),
        sa.Column("complaint_id", sa.String(128), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(128),
            sa.ForeignKey("sales.customers.customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("assigned_to", sa.String(128), nullable=True),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "pricings",
        *_scope(),
        sa.Column("pricing_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "pricing_items",
        *_scope(),
        sa.Column("pricing_item_id", sa.String(128), primary_key=True),
        sa.Column("pricing_id", sa.String(128), nullable=False, index=True),
        sa.Column("property_unit_id", sa.String(128), nullable=False, index=True),
        sa.Column("price", sa.Numeric(20, 2), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False, server_default="IDR"),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "collaterals",
        *_scope(),
        sa.Column("collateral_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("collateral_type", sa.String(128), nullable=False),
        sa.Column("campaign_id", sa.String(128), nullable=True, index=True),
        sa.Column("document_id", sa.String(128), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="sales",
    )
    op.create_table(
        "campaigns",
        *_scope(),
        sa.Column("campaign_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("campaign_type", sa.String(128), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("budget", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PLANNED"),
        *_dates(),
        schema="marketing",
    )
    op.create_table(
        "channels",
        *_scope(),
        sa.Column("channel_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("channel_type", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        sa.UniqueConstraint("tenant_id", "organization_id", "name", name="uq_marketing_channel"),
        schema="marketing",
    )
    op.create_table(
        "attributions",
        *_scope(),
        sa.Column("attribution_id", sa.String(128), primary_key=True),
        sa.Column("customer_id", sa.String(128), nullable=True, index=True),
        sa.Column("lead_id", sa.String(128), nullable=True, index=True),
        sa.Column("campaign_id", sa.String(128), nullable=True, index=True),
        sa.Column("channel_id", sa.String(128), nullable=True, index=True),
        sa.Column("touch_type", sa.String(128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        *_dates(),
        schema="marketing",
    )
    op.create_table(
        "contents",
        *_scope(),
        sa.Column("content_id", sa.String(128), primary_key=True),
        sa.Column("campaign_id", sa.String(128), nullable=True, index=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="marketing",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
