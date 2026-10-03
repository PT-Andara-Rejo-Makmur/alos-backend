"""Persist public operational progress without model reasoning or prompts."""

import sqlalchemy as sa
from alembic import op

revision = "0038_ara_progress"
down_revision = "0037_business_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ara_progress_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("core.ara_runs.run_id"), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "event_key", name="uq_ara_progress_event"),
        sa.CheckConstraint(
            "kind IN ('UNDERSTANDING','RETRIEVING','ANALYZING','PREPARING',"
            "'WAITING_FOR_REVIEW','COMPLETED','FAILED')",
            name="ck_ara_progress_kind",
        ),
        schema="core",
    )
    op.create_index(
        "ix_ara_progress_run", "ara_progress_events", ["run_id", "event_id"], schema="core"
    )


def downgrade() -> None:
    raise RuntimeError("Preserve conversation progress evidence; use forward correction")
