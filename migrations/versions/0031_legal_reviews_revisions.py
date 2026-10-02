"""Append minimal internal records identified by canonical gap analysis."""

import sqlalchemy as sa
from alembic import op

revision = "0031_legal_reviews_revisions"
down_revision = "0030_ga_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legal_reviews",
        sa.Column("legal_review_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("contract_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("review_summary", sa.Text(), nullable=True),
        sa.Column("assessment", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["legal.contracts.contract_id"]),
        schema="legal",
    )
    op.create_index(
        "ix_legal_reviews_scope",
        "legal_reviews",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="legal",
    )
    op.create_table(
        "contract_revisions",
        sa.Column("contract_revision_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("contract_id", sa.String(128), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("document_version", sa.String(100), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("recorded_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["legal.contracts.contract_id"]),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "workspace_id", "contract_id", "revision_number"
        ),
        schema="legal",
    )
    op.create_index(
        "ix_contract_revisions_scope",
        "contract_revisions",
        ["tenant_id", "organization_id", "workspace_id", "updated_at"],
        schema="legal",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
