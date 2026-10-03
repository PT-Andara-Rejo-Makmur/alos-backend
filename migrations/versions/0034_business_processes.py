"""Persist business routing, scoped packets, explicit actions and immutable history."""

import sqlalchemy as sa
from alembic import op

revision = "0034_business_processes"
down_revision = "0033_ara_runtime_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_process_policies",
        sa.Column("policy_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("business_type", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("routes", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "workspace_id",
            "business_type",
            name="uq_business_process_policy",
        ),
        schema="core",
    )
    op.create_table(
        "business_processes",
        sa.Column("process_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("business_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column(
            "requested_by", sa.String(128), sa.ForeignKey("core.actors.actor_id"), nullable=False
        ),
        sa.Column(
            "policy_id",
            sa.String(128),
            sa.ForeignKey("core.business_process_policies.policy_id"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("subject_snapshot", sa.String(64), nullable=False),
        sa.Column("packet", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "workspace_id",
            "business_type",
            "subject_id",
            name="uq_business_process_subject",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','READY','IN_PROGRESS','COMPLETED','RETURNED','CANCELLED')",
            name="ck_business_process_status",
        ),
        schema="core",
    )
    op.create_table(
        "business_process_steps",
        sa.Column("step_id", sa.String(128), primary_key=True),
        sa.Column(
            "process_id",
            sa.String(128),
            sa.ForeignKey("core.business_processes.process_id"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("permission", sa.String(128), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("independent", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "process_id", "revision", "position", name="uq_business_process_step_position"
        ),
        sa.CheckConstraint(
            "kind IN ('REVIEW','DECISION','EXECUTION','ACKNOWLEDGEMENT')",
            name="ck_business_step_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','READY','IN_PROGRESS','COMPLETED',"
            "'RETURNED','SKIPPED','CANCELLED')",
            name="ck_business_step_status",
        ),
        schema="core",
    )
    op.create_index(
        "ix_business_steps_queue",
        "business_process_steps",
        ["workspace_id", "role", "status", "due_at"],
        schema="core",
    )
    op.create_table(
        "business_process_history",
        sa.Column("history_id", sa.String(128), primary_key=True),
        sa.Column(
            "process_id",
            sa.String(128),
            sa.ForeignKey("core.business_processes.process_id"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(128),
            sa.ForeignKey("core.business_process_steps.step_id"),
            nullable=True,
        ),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("role_refs", sa.JSON(), nullable=False),
        sa.Column("permission_refs", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_index(
        "ix_business_history_process",
        "business_process_history",
        ["process_id", "occurred_at"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only; retain business history")
