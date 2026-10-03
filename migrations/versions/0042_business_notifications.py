"""Persist bounded in-app notifications delivered by the existing SQL job queue."""

import sqlalchemy as sa
from alembic import op

revision = "0042_business_notifications"
down_revision = "0041_certificate_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_notifications",
        sa.Column("notification_id", sa.String(128), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(128),
            sa.ForeignKey("jobs.queue.job_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column(
            "process_id",
            sa.String(128),
            sa.ForeignKey("core.business_processes.process_id"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(128), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("role_refs", sa.JSON(), nullable=False),
        sa.Column("permission", sa.String(128), nullable=False),
        sa.Column("recipient_actor_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_index(
        "ix_business_notification_audience",
        "business_notifications",
        ["tenant_id", "organization_id", "workspace_id", "created_at"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("Retain delivered business notices; use an additive correction migration.")
