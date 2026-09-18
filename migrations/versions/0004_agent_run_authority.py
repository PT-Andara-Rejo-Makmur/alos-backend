"""Add authoritative Agent run lifecycle records.

Revision ID: 0004_agent_run_authority
Revises: 0003_tool_governance_release
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_agent_run_authority"
down_revision = "0003_tool_governance_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "ai_runtime"'))
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("root_run_id", sa.String(128), nullable=False, index=True),
        sa.Column("parent_run_id", sa.String(128), nullable=True, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False, index=True),
        sa.Column("agent_id", sa.String(128), nullable=False, index=True),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("capability_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("registry_digest", sa.String(64), nullable=False),
        sa.Column("lifecycle_authorization", sa.String(32), nullable=False),
        sa.Column("authorized_tool_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="ai_runtime",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
