"""Append minimal internal records identified by canonical gap analysis."""

import sqlalchemy as sa
from alembic import op

revision = "0030_ga_records"
down_revision = "0029_material_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "facility_requests",
        sa.Column("facility_request_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("facility_code", sa.String(128), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("needed_on", sa.Date(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="hr",
    )
    op.create_index(
        "ix_facility_requests_scope",
        "facility_requests",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="hr",
    )
    op.create_table(
        "inventory_items",
        sa.Column("inventory_item_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("asset_code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("condition", sa.String(64), nullable=False),
        sa.Column("recorded_on", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "organization_id", "workspace_id", "asset_code"),
        schema="hr",
    )
    op.create_index(
        "ix_inventory_items_scope",
        "inventory_items",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="hr",
    )
    op.create_table(
        "asset_handovers",
        sa.Column("asset_handover_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("inventory_item_id", sa.String(128), nullable=False),
        sa.Column("employee_id", sa.String(128), nullable=False),
        sa.Column("handover_on", sa.Date(), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["hr.inventory_items.inventory_item_id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employees.employee_id"]),
        schema="hr",
    )
    op.create_index(
        "ix_asset_handovers_scope",
        "asset_handovers",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="hr",
    )
    op.create_table(
        "maintenance_records",
        sa.Column("maintenance_record_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("inventory_item_id", sa.String(128), nullable=False),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("result", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["hr.inventory_items.inventory_item_id"]),
        schema="hr",
    )
    op.create_index(
        "ix_maintenance_records_scope",
        "maintenance_records",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="hr",
    )
    op.create_table(
        "service_assessments",
        sa.Column("service_assessment_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("facility_code", sa.String(128), nullable=False),
        sa.Column("assessed_on", sa.Date(), nullable=False),
        sa.Column("readiness", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="hr",
    )
    op.create_index(
        "ix_service_assessments_scope",
        "service_assessments",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="hr",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
