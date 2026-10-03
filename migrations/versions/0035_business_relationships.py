"""Add traceable business origins and atomic Sales reservation references."""

import sqlalchemy as sa
from alembic import op

revision = "0035_business_relationships"
down_revision = "0034_business_processes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "opportunity_id",
            sa.String(128),
            sa.ForeignKey("sales.opportunities.opportunity_id"),
            nullable=True,
        ),
        schema="sales",
    )
    op.create_index("ix_sales_booking_opportunity", "bookings", ["opportunity_id"], schema="sales")
    op.add_column(
        "property_units",
        sa.Column(
            "reservation_booking_id",
            sa.String(128),
            sa.ForeignKey("sales.bookings.booking_id"),
            nullable=True,
        ),
        schema="property",
    )
    op.create_index(
        "uq_property_reservation_booking",
        "property_units",
        ["reservation_booking_id"],
        unique=True,
        schema="property",
    )
    op.add_column(
        "change_orders",
        sa.Column("schedule_impact_days", sa.Integer(), nullable=True),
        schema="property",
    )
    op.add_column(
        "change_orders",
        sa.Column(
            "related_contract_id",
            sa.String(128),
            sa.ForeignKey("legal.contracts.contract_id"),
            nullable=True,
        ),
        schema="property",
    )
    op.create_table(
        "business_record_links",
        sa.Column("link_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "source_workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column(
            "target_workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column(
            "process_id",
            sa.String(128),
            sa.ForeignKey("core.business_processes.process_id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "source_type",
            "source_id",
            "target_type",
            "relation",
            name="uq_business_origin_target",
        ),
        schema="core",
    )
    op.create_index(
        "ix_business_link_target",
        "business_record_links",
        ["tenant_id", "organization_id", "target_workspace_id", "target_type", "target_id"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only; retain business lineage")
