"""Persist business needs and Factory resolution without duplicating governance."""

import sqlalchemy as sa
from alembic import op

revision = "0048_capability_requests"
down_revision = "0047_ara_reviewed_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability_business_requests",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by", sa.String(128), sa.ForeignKey("core.actors.actor_id"), nullable=False
        ),
        sa.Column("need", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("business_context", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), server_default="SUBMITTED", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_table(
        "capability_request_resolutions",
        sa.Column(
            "request_id",
            sa.String(128),
            sa.ForeignKey("core.capability_business_requests.request_id"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column(
            "resolved_by", sa.String(128), sa.ForeignKey("core.actors.actor_id"), nullable=False
        ),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column(
            "review_id",
            sa.String(128),
            sa.ForeignKey("core.review_packages.review_id"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("Preserve business requests and Factory receipts; use additive corrections.")
