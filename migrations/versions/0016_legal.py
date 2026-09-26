"""Add Legal and Compliance workspace domain persistence.

Revision ID: 0016_legal
Revises: 0015_hr
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_legal"
down_revision = "0015_hr"
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
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "legal"'))

    op.create_table(
        "permits",
        *_scope(),
        sa.Column("permit_id", sa.String(128), primary_key=True),
        sa.Column("permit_type", sa.String(128), nullable=False),
        sa.Column("permit_number", sa.String(200), nullable=True),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "contracts",
        *_scope(),
        sa.Column("contract_id", sa.String(128), primary_key=True),
        sa.Column("contract_number", sa.String(200), nullable=False),
        sa.Column("contract_type", sa.String(128), nullable=False),
        sa.Column("counterparty_name", sa.String(300), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        sa.Column("document_id", sa.String(128), nullable=True, index=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "land_documents",
        *_scope(),
        sa.Column("land_document_id", sa.String(128), primary_key=True),
        sa.Column("property_ref", sa.String(128), nullable=True, index=True),
        sa.Column("document_type", sa.String(128), nullable=False),
        sa.Column("document_number", sa.String(200), nullable=True),
        sa.Column("holder_name", sa.String(300), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        sa.Column("document_id", sa.String(128), nullable=True, index=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "due_diligences",
        *_scope(),
        sa.Column("due_diligence_id", sa.String(128), primary_key=True),
        sa.Column("subject_type", sa.String(128), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "due_diligence_items",
        *_scope(),
        sa.Column("due_diligence_item_id", sa.String(128), primary_key=True),
        sa.Column(
            "due_diligence_id",
            sa.String(128),
            sa.ForeignKey("legal.due_diligences.due_diligence_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("item_type", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("finding", sa.Text(), nullable=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "cases",
        *_scope(),
        sa.Column("case_id", sa.String(128), primary_key=True),
        sa.Column("case_number", sa.String(200), nullable=False),
        sa.Column("case_type", sa.String(128), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "claim_reviews",
        *_scope(),
        sa.Column("claim_review_id", sa.String(128), primary_key=True),
        sa.Column("subject_type", sa.String(128), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("claimant_name", sa.String(300), nullable=True),
        sa.Column("claim_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "expiries",
        *_scope(),
        sa.Column("expiry_id", sa.String(128), primary_key=True),
        sa.Column("subject_type", sa.String(128), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("expires_at", sa.Date(), nullable=False, index=True),
        sa.Column("reminder_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "privacy_requests",
        *_scope(),
        sa.Column("privacy_request_id", sa.String(128), primary_key=True),
        sa.Column("request_type", sa.String(128), nullable=False),
        sa.Column("requester_ref", sa.String(128), nullable=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "risks",
        *_scope(),
        sa.Column("risk_id", sa.String(128), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("likelihood", sa.String(32), nullable=False),
        sa.Column("impact", sa.String(32), nullable=False),
        sa.Column("rating", sa.String(32), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="legal",
    )
    op.create_table(
        "controls",
        *_scope(),
        sa.Column("control_id", sa.String(128), primary_key=True),
        sa.Column("control_code", sa.String(128), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("frequency", sa.String(64), nullable=True),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "control_code", name="uq_legal_control_code"
        ),
        schema="legal",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
