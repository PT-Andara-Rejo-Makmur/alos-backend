"""Clarify project objectives and ownership and reuse tasks for process work."""

import sqlalchemy as sa
from alembic import op

revision = "0043_project_responsibility"
down_revision = "0042_business_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("objective", sa.Text(), nullable=True), schema="core")
    op.add_column(
        "projects",
        sa.Column("priority", sa.String(16), nullable=False, server_default="NORMAL"),
        schema="core",
    )
    op.add_column(
        "projects",
        sa.Column(
            "owning_workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=True,
        ),
        schema="core",
    )
    op.create_check_constraint(
        "ck_project_priority",
        "projects",
        "priority IN ('LOW','NORMAL','HIGH','CRITICAL')",
        schema="core",
    )
    op.add_column(
        "business_process_steps",
        sa.Column("task_id", sa.String(128), sa.ForeignKey("core.tasks.task_id"), nullable=True),
        schema="core",
    )
    op.create_index(
        "ix_business_process_task", "business_process_steps", ["task_id"], schema="core"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Retain project responsibility and task lineage; use an additive correction migration."
    )
