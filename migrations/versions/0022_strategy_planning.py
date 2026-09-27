"""Create authoritative strategy planning domain.

Revision ID: 0022_strategy_planning
Revises: 0021_domain_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_strategy_planning"
down_revision = "0021_domain_indexes"
branch_labels = None
depends_on = None


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS strategy")
    op.create_table(
        "plans",
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        *_scope_columns(),
        sa.PrimaryKeyConstraint("plan_id", "version"),
        schema="strategy",
    )
    op.create_table(
        "objectives",
        sa.Column("objective_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        *_scope_columns(),
        sa.PrimaryKeyConstraint("objective_id", "version"),
        schema="strategy",
    )
    op.create_table(
        "targets",
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        *_scope_columns(),
        sa.PrimaryKeyConstraint("target_id", "version"),
        schema="strategy",
    )
    op.create_table(
        "target_observations",
        sa.Column("observation_id", sa.String(128), primary_key=True),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        *_scope_columns(),
        schema="strategy",
    )
    op.create_table(
        "target_relationships",
        sa.Column("relationship_id", sa.String(128), primary_key=True),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("parent_target_id", sa.String(128), nullable=False),
        sa.Column("parent_target_version", sa.Integer(), nullable=False),
        sa.Column("child_target_id", sa.String(128), nullable=False),
        sa.Column("child_target_version", sa.Integer(), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "relationship_type",
            "parent_target_id",
            "parent_target_version",
            "child_target_id",
            "child_target_version",
            name="uq_strategy_target_relationship_exact_versions",
        ),
        schema="strategy",
    )
    op.create_table(
        "planning_assumptions",
        sa.Column("assumption_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_scope_columns(),
        sa.PrimaryKeyConstraint("assumption_id", "version"),
        schema="strategy",
    )
    _payload_table("cascade_rules", "cascade_rule_id")
    op.create_table(
        "cascade_runs",
        sa.Column("cascade_run_id", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        *_scope_columns(),
        schema="strategy",
    )
    _payload_table("cascade_results", "cascade_result_id")
    _payload_table("planning_constraints", "constraint_id")
    _payload_table("constraint_results", "constraint_result_id")
    _payload_table("kpi_definitions", "kpi_id")
    _payload_table("initiatives", "initiative_id")
    op.create_table(
        "target_revisions",
        sa.Column("revision_id", sa.String(128), primary_key=True),
        sa.Column("target_id", sa.String(128), nullable=False),
        *_scope_columns(),
        schema="strategy",
    )
    indexes = [
        (
            "ix_strategy_plans_scope_state",
            "plans",
            ["tenant_id", "organization_id", "lifecycle_state"],
        ),
        ("ix_strategy_objectives_plan", "objectives", ["tenant_id", "organization_id", "plan_id"]),
        (
            "ix_strategy_targets_plan_state",
            "targets",
            ["tenant_id", "organization_id", "plan_id", "lifecycle_state"],
        ),
        (
            "ix_strategy_observations_target",
            "target_observations",
            ["tenant_id", "organization_id", "target_id", "target_version"],
        ),
        (
            "ix_strategy_assumptions_scope",
            "planning_assumptions",
            ["tenant_id", "organization_id", "workspace_id"],
        ),
        (
            "ix_strategy_relationship_parent",
            "target_relationships",
            ["tenant_id", "organization_id", "parent_target_id", "parent_target_version"],
        ),
        (
            "ix_strategy_relationship_child",
            "target_relationships",
            ["tenant_id", "organization_id", "child_target_id", "child_target_version"],
        ),
        ("ix_strategy_cascade_status", "cascade_runs", ["tenant_id", "organization_id", "status"]),
        (
            "ix_strategy_revisions_target",
            "target_revisions",
            ["tenant_id", "organization_id", "target_id"],
        ),
    ]
    for name, table, columns in indexes:
        op.create_index(name, table, columns, schema="strategy")


def _payload_table(table: str, identity: str) -> None:
    op.create_table(
        table,
        sa.Column(identity, sa.String(128), primary_key=True),
        *_scope_columns(),
        schema="strategy",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
