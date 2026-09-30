"""Persist Shared Work metadata and reusable relations.

Revision ID: 0025_shared_work_details
Revises: 0024_approval_decision_reason
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_shared_work_details"
down_revision = "0024_approval_decision_reason"
branch_labels = None
depends_on = None


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("tasks", sa.Column("start_date", sa.Date(), nullable=True), schema="core")
    op.add_column("documents", sa.Column("description", sa.Text(), nullable=True), schema="core")
    op.add_column(
        "documents",
        sa.Column("project_id", sa.String(128), sa.ForeignKey("core.projects.project_id")),
        schema="core",
    )
    op.add_column("documents", sa.Column("effective_date", sa.Date()), schema="core")
    op.add_column("documents", sa.Column("expiry_date", sa.Date()), schema="core")
    op.add_column("documents", sa.Column("updated_at", sa.DateTime(timezone=True)), schema="core")
    op.create_index("ix_documents_project_id", "documents", ["project_id"], schema="core")
    op.add_column("work_reports", sa.Column("description", sa.Text()), schema="core")
    op.add_column("work_reports", sa.Column("period_start", sa.Date()), schema="core")
    op.add_column("work_reports", sa.Column("period_end", sa.Date()), schema="core")
    op.add_column("work_reports", sa.Column("scope", sa.Text()), schema="core")
    op.add_column(
        "work_reports", sa.Column("published_at", sa.DateTime(timezone=True)), schema="core"
    )
    op.add_column(
        "work_reports",
        sa.Column("project_id", sa.String(128), sa.ForeignKey("core.projects.project_id")),
        schema="core",
    )
    op.create_index("ix_work_reports_project_id", "work_reports", ["project_id"], schema="core")
    op.add_column("work_findings", sa.Column("category", sa.String(128)), schema="core")
    op.add_column(
        "work_findings",
        sa.Column("project_id", sa.String(128), sa.ForeignKey("core.projects.project_id")),
        schema="core",
    )
    op.add_column(
        "work_findings", sa.Column("identified_at", sa.DateTime(timezone=True)), schema="core"
    )
    op.execute(sa.text("UPDATE core.work_findings SET identified_at = created_at"))
    op.alter_column(
        "work_findings", "identified_at", nullable=False,
        server_default=sa.text("now()"), schema="core",
    )
    op.add_column("work_findings", sa.Column("due_date", sa.Date()), schema="core")
    op.add_column("work_findings", sa.Column("impact", sa.Text()), schema="core")
    op.add_column("work_findings", sa.Column("root_cause", sa.Text()), schema="core")
    op.add_column(
        "work_findings",
        sa.Column("corrective_action_task_id", sa.String(128), sa.ForeignKey("core.tasks.task_id")),
        schema="core",
    )
    op.add_column("work_findings", sa.Column("verifier_actor_id", sa.String(128)), schema="core")
    op.add_column(
        "work_findings", sa.Column("verified_at", sa.DateTime(timezone=True)), schema="core"
    )
    op.create_index("ix_work_findings_project_id", "work_findings", ["project_id"], schema="core")
    op.create_index(
        "ix_work_findings_corrective_task",
        "work_findings",
        ["corrective_action_task_id"],
        schema="core",
    )
    op.create_table(
        "work_report_definitions",
        sa.Column("report_definition_id", sa.String(128), primary_key=True),
        *_scope(),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("report_type", sa.String(128), nullable=False),
        sa.Column("frequency", sa.String(32), nullable=False),
        sa.Column("scope", sa.Text()),
        sa.Column("owner_actor_id", sa.String(128), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recipients", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sections", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("data_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("schedule_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["core.workspaces.workspace_id"]),
        schema="core",
    )
    op.create_index(
        "ix_work_report_definitions_scope",
        "work_report_definitions",
        ["tenant_id", "organization_id", "workspace_id"],
        schema="core",
    )
    op.create_table(
        "shared_work_evidence_links",
        sa.Column("link_id", sa.String(128), primary_key=True),
        *_scope(),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column(
            "evidence_id",
            sa.String(128),
            sa.ForeignKey("evidence.evidence_refs.evidence_id"),
            nullable=False,
        ),
        sa.Column("linked_by", sa.String(128), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "workspace_id",
            "entity_type",
            "entity_id",
            "evidence_id",
            name="uq_shared_work_evidence_link",
        ),
        schema="core",
    )
    op.create_index(
        "ix_shared_work_evidence_entity",
        "shared_work_evidence_links",
        ["tenant_id", "organization_id", "workspace_id", "entity_type", "entity_id"],
        schema="core",
    )
    op.create_table(
        "shared_work_comments",
        sa.Column("comment_id", sa.String(128), primary_key=True),
        *_scope(),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_index(
        "ix_shared_work_comments_entity",
        "shared_work_comments",
        ["tenant_id", "organization_id", "workspace_id", "entity_type", "entity_id"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
