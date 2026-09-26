"""Add missing GENESIS governance persistence.

Revision ID: 0020_genesis_governance
Revises: 0019_it_operations
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_genesis_governance"
down_revision = "0019_it_operations"
branch_labels = None
depends_on = None


def _scope(schema="core"):
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            index=True,
        ),
    ]


def _dates():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "genesis"'))

    op.create_table(
        "agent_skills",
        *_scope(),
        sa.Column("agent_skill_id", sa.String(128), primary_key=True),
        sa.Column("agent_subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("skill_subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("skill_version", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "agent_subject_id",
            "skill_subject_id",
            name="uq_genesis_agent_skill",
        ),
        schema="genesis",
    )
    op.create_table(
        "agent_models",
        *_scope(),
        sa.Column("agent_model_id", sa.String(128), primary_key=True),
        sa.Column("agent_subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("model_subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "agent_subject_id",
            "model_subject_id",
            name="uq_genesis_agent_model",
        ),
        schema="genesis",
    )
    op.create_table(
        "agent_tools",
        *_scope(),
        sa.Column("agent_tool_id", sa.String(128), primary_key=True),
        sa.Column("agent_subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("tool_subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "agent_subject_id",
            "tool_subject_id",
            name="uq_genesis_agent_tool",
        ),
        schema="genesis",
    )
    op.create_table(
        "uat_gates",
        *_scope(),
        sa.Column("uat_gate_id", sa.String(128), primary_key=True),
        sa.Column("subject_type", sa.String(128), nullable=False, index=True),
        sa.Column("subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("gate_code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "subject_type",
            "subject_id",
            "gate_code",
            name="uq_genesis_uat_gate",
        ),
        schema="genesis",
    )
    op.create_table(
        "uat_results",
        *_scope(),
        sa.Column("uat_result_id", sa.String(128), primary_key=True),
        sa.Column(
            "uat_gate_id",
            sa.String(128),
            sa.ForeignKey("genesis.uat_gates.uat_gate_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("run_id", sa.String(128), nullable=True, index=True),
        sa.Column("result", sa.String(32), nullable=False, index=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("executed_by", sa.String(128), nullable=False, index=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        *_dates(),
        schema="genesis",
    )
    op.create_table(
        "technical_decisions",
        *_scope(),
        sa.Column("technical_decision_id", sa.String(128), primary_key=True),
        sa.Column("decision_code", sa.String(128), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        sa.Column("decided_by", sa.String(128), nullable=True, index=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "decision_code", name="uq_genesis_technical_decision"
        ),
        schema="genesis",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
