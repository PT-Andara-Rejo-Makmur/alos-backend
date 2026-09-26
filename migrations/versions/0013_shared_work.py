"""Add canonical cross-workspace work entities.

Revision ID: 0013_shared_work
Revises: 0012_navigation
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_shared_work"
down_revision = "0012_navigation"
branch_labels = None
depends_on = None


def _org_scope():
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
    ]


def _dates():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "projects",
        *_org_scope(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PLANNED"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True, index=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_end_date", sa.Date(), nullable=True),
        *_dates(),
        sa.UniqueConstraint("tenant_id", "organization_id", "code", name="uq_project_scope_code"),
        schema="core",
    )
    op.create_table(
        "project_workspaces",
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            primary_key=True,
        ),
        schema="core",
    )
    op.create_table(
        "tasks",
        *_org_scope(),
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("core.projects.project_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("priority", sa.String(32), nullable=False, server_default="NORMAL"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True, index=True),
        sa.Column("created_by", sa.String(128), nullable=False, index=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True, index=True),
        *_dates(),
        schema="core",
    )
    op.create_table(
        "task_workspaces",
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("core.tasks.task_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            primary_key=True,
        ),
        schema="core",
    )
    op.create_table(
        "work_approvals",
        *_org_scope(),
        sa.Column("approval_id", sa.String(128), primary_key=True),
        sa.Column("subject_type", sa.String(128), nullable=False, index=True),
        sa.Column("subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("requested_by", sa.String(128), nullable=False, index=True),
        sa.Column("approver_actor_id", sa.String(128), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PENDING"),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    op.create_table(
        "work_approval_workspaces",
        sa.Column(
            "approval_id",
            sa.String(128),
            sa.ForeignKey("core.work_approvals.approval_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            primary_key=True,
        ),
        schema="core",
    )
    op.create_table(
        "work_reports",
        *_org_scope(),
        sa.Column("report_id", sa.String(128), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("report_type", sa.String(128), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True, index=True),
        *_dates(),
        schema="core",
    )
    op.create_table(
        "work_report_workspaces",
        sa.Column(
            "report_id",
            sa.String(128),
            sa.ForeignKey("core.work_reports.report_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            primary_key=True,
        ),
        schema="core",
    )
    op.create_table(
        "work_findings",
        *_org_scope(),
        sa.Column("finding_id", sa.String(128), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(32), nullable=False, index=True, server_default="MEDIUM"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("source_type", sa.String(64), nullable=False, server_default="MANUAL"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True, index=True),
        *_dates(),
        schema="core",
    )
    op.create_table(
        "work_finding_workspaces",
        sa.Column(
            "finding_id",
            sa.String(128),
            sa.ForeignKey("core.work_findings.finding_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            primary_key=True,
        ),
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
