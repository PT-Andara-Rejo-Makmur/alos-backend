"""Add Property and Project workspace domain persistence.

Revision ID: 0018_property
Revises: 0017_sales_marketing
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_property"
down_revision = "0017_sales_marketing"
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
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "property"'))

    op.create_table(
        "property_units",
        *_scope(),
        sa.Column("property_unit_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("unit_code", sa.String(128), nullable=False),
        sa.Column("unit_name", sa.String(300), nullable=True),
        sa.Column("unit_type", sa.String(128), nullable=True),
        sa.Column("area_land", sa.Numeric(16, 2), nullable=True),
        sa.Column("area_building", sa.Numeric(16, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="AVAILABLE"),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "unit_code", name="uq_property_unit_code"
        ),
        schema="property",
    )
    op.create_table(
        "project_milestones",
        *_scope(),
        sa.Column("milestone_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "construction_packages",
        *_scope(),
        sa.Column("construction_package_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("package_code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("contractor_name", sa.String(300), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PLANNED"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "construction_updates",
        *_scope(),
        sa.Column("construction_update_id", sa.String(128), primary_key=True),
        sa.Column(
            "construction_package_id",
            sa.String(128),
            sa.ForeignKey("property.construction_packages.construction_package_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("update_date", sa.Date(), nullable=False, index=True),
        sa.Column("progress_percent", sa.Numeric(8, 2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="RECORDED"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "quality_inspections",
        *_scope(),
        sa.Column("inspection_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("inspection_type", sa.String(128), nullable=False),
        sa.Column("inspection_date", sa.Date(), nullable=False),
        sa.Column("inspector_actor_id", sa.String(128), nullable=True),
        sa.Column("result", sa.String(32), nullable=False, index=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "quality_ncrs",
        *_scope(),
        sa.Column("ncr_id", sa.String(128), primary_key=True),
        sa.Column(
            "inspection_id",
            sa.String(128),
            sa.ForeignKey("property.quality_inspections.inspection_id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "safety_incidents",
        *_scope(),
        sa.Column("safety_incident_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("incident_date", sa.Date(), nullable=False, index=True),
        sa.Column("severity", sa.String(32), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "change_orders",
        *_scope(),
        sa.Column("change_order_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("change_number", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount_delta", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "payment_certificates",
        *_scope(),
        sa.Column("payment_certificate_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("certificate_number", sa.String(128), nullable=False),
        sa.Column("period", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "project_handovers",
        *_scope(),
        sa.Column("handover_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("handover_type", sa.String(64), nullable=False),
        sa.Column("handover_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PLANNED"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="property",
    )
    op.create_table(
        "land_pipeline",
        *_scope(),
        sa.Column("land_pipeline_id", sa.String(128), primary_key=True),
        sa.Column("location", sa.String(500), nullable=False),
        sa.Column("area", sa.Numeric(16, 2), nullable=True),
        sa.Column("owner_name", sa.String(300), nullable=True),
        sa.Column("stage", sa.String(64), nullable=False, index=True, server_default="IDENTIFIED"),
        sa.Column("estimated_value", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="property",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
