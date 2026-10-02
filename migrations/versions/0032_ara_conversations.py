"""Authoritative ARA conversations, messages and run linkage."""

import sqlalchemy as sa
from alembic import op

revision = "0032_ara_conversations"
down_revision = "0031_legal_reviews_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ara_threads",
        sa.Column("thread_id", sa.String(128), primary_key=True),
        *[
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "organization_id", "workspace_id", "actor_id")
        ],
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("active_run_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_index(
        "ix_ara_thread_boundary",
        "ara_threads",
        ["tenant_id", "organization_id", "workspace_id", "actor_id", "updated_at"],
        schema="core",
    )
    op.create_table(
        "ara_messages",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column(
            "thread_id", sa.String(128), sa.ForeignKey("core.ara_threads.thread_id"), nullable=False
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("response", sa.JSON()),
        schema="core",
    )
    for field in ("thread_id", "run_id"):
        op.create_index(f"ix_core_ara_messages_{field}", "ara_messages", [field], schema="core")
    op.create_table(
        "ara_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column(
            "thread_id", sa.String(128), sa.ForeignKey("core.ara_threads.thread_id"), nullable=False
        ),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response", sa.JSON()),
        schema="core",
    )
    op.create_index("ix_core_ara_runs_thread_id", "ara_runs", ["thread_id"], schema="core")


def downgrade() -> None:
    for table in ("ara_runs", "ara_messages", "ara_threads"):
        op.drop_table(table, schema="core")
