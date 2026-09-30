"""Persist task dependencies and optional approval materiality.

Revision ID: 0027_task_dependencies
Revises: 0026_shared_work_links
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_task_dependencies"
down_revision = "0026_shared_work_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_approvals", sa.Column("materiality_value", sa.Numeric(18, 2)), schema="core"
    )
    op.create_check_constraint(
        "ck_work_approvals_materiality_nonnegative", "work_approvals",
        "materiality_value IS NULL OR materiality_value >= 0", schema="core",
    )
    op.create_table(
        "shared_work_task_dependencies",
        sa.Column("dependency_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column(
            "task_id", sa.String(128), sa.ForeignKey("core.tasks.task_id"), nullable=False
        ),
        sa.Column(
            "blocked_by_task_id", sa.String(128),
            sa.ForeignKey("core.tasks.task_id"), nullable=False,
        ),
        sa.Column("linked_by", sa.String(128), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("task_id <> blocked_by_task_id", name="ck_task_dependency_not_self"),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "workspace_id", "task_id", "blocked_by_task_id",
            name="uq_shared_work_task_dependency",
        ),
        schema="core",
    )
    op.create_index(
        "ix_shared_work_task_dependencies_blocker", "shared_work_task_dependencies",
        ["tenant_id", "organization_id", "workspace_id", "blocked_by_task_id"], schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
