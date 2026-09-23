"""Align runtime and research persistence with authoritative ORM metadata.

Revision ID: 0007_runtime_research_persistence
Revises: 0006_auth_accounts
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_runtime_research_persistence"
down_revision = "0006_auth_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "research"'))
    additions = (
        sa.Column("delegation_id", sa.String(128), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancellation_state", sa.String(32), nullable=False, server_default="NONE"),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("usage_ref", sa.String(128), nullable=True),
        sa.Column("cost_ref", sa.String(128), nullable=True),
        sa.Column("structured_result_ref", sa.String(128), nullable=True),
        sa.Column("model_provider", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("model_cost", sa.Float(), nullable=True),
        sa.Column("tool_cost", sa.Float(), nullable=True),
        sa.Column("total_cost", sa.Float(), nullable=True),
        sa.Column("budget_limit", sa.Float(), nullable=True),
        sa.Column("remaining_budget", sa.Float(), nullable=True),
    )
    for column in additions:
        op.add_column("agent_runs", column, schema="ai_runtime")
    op.create_index(
        "ix_agent_runs_delegation_id", "agent_runs", ["delegation_id"], schema="ai_runtime"
    )
    op.create_index("ix_agent_runs_depth", "agent_runs", ["depth"], schema="ai_runtime")

    op.create_table(
        "agent_run_steps",
        sa.Column("step_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, index=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_type", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("correlation_id", sa.String(128), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tool_id", sa.String(128), nullable=True),
        sa.Column("input_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("output_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("tool_cost", sa.Float(), nullable=True),
        schema="ai_runtime",
    )
    op.create_table(
        "backlog_candidates",
        sa.Column("candidate_id", sa.String(128), primary_key=True),
        sa.Column("finding_id", sa.String(128), nullable=False, index=True),
        sa.Column("recommendation_id", sa.String(128), nullable=False, index=True),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("priority_suggestion", sa.String(32), nullable=False),
        sa.Column("owner_suggestion", sa.String(128), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("approval_state", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("actor_id", sa.String(128), nullable=False, index=True),
        sa.Column("scope_ref", sa.String(128), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        schema="ai_runtime",
    )
    op.create_table(
        "research_findings",
        sa.Column("finding_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True, index=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_ref", sa.String(200), nullable=True),
        sa.Column("retrieval_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="research",
    )
    op.create_table(
        "research_recommendations",
        sa.Column("recommendation_id", sa.String(128), primary_key=True),
        sa.Column("finding_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True, index=True),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("priority_suggestion", sa.String(32), nullable=False),
        sa.Column("owner_suggestion", sa.String(128), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="research",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
