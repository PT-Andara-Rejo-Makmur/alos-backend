"""Add governed tools, idempotency, and release lifecycle controls.

Revision ID: 0003_tool_governance_release
Revises: 0002_authority_registries
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_tool_governance_release"
down_revision = "0002_authority_registries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "governance"'))

    release_columns = (
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("organization_id", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("workspace_id", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("subject_id", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("subject_version", sa.String(64), nullable=False, server_default="0.0.0"),
        sa.Column("materiality", sa.String(32), nullable=False, server_default="NON_MATERIAL"),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("it_decision_id", sa.String(128), nullable=True),
        sa.Column("director_decision_id", sa.String(128), nullable=True),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rollback_target_release_id", sa.String(128), nullable=True),
        sa.Column("ever_released", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for column in release_columns:
        op.add_column("releases", column, schema="core")

    op.create_index("ix_releases_tenant_id", "releases", ["tenant_id"], schema="core")
    op.create_index("ix_releases_organization_id", "releases", ["organization_id"], schema="core")
    op.create_index("ix_releases_workspace_id", "releases", ["workspace_id"], schema="core")
    op.create_index("ix_releases_subject_id", "releases", ["subject_id"], schema="core")
    op.create_index("ix_releases_correlation_id", "releases", ["correlation_id"], schema="core")

    op.create_table(
        "tool_definitions",
        sa.Column("tool_definition_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("tool_id", sa.String(128), nullable=False, index=True),
        sa.Column("required_permission", sa.String(200), nullable=False),
        sa.Column("required_scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("lifecycle_state", sa.String(32), nullable=False, index=True),
        sa.Column("idempotency_policy", sa.String(32), nullable=False),
        sa.Column("allowlisted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("production_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="5"),
        sa.Column("adapter_key", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "tool_id", name="uq_tool_definition_tenant"),
        schema="core",
    )
    op.create_table(
        "tool_idempotency",
        sa.Column("idempotency_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("tool_id", sa.String(128), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "tool_id",
            "idempotency_key",
            name="uq_tool_idempotency_authority",
        ),
        schema="governance",
    )
    op.create_table(
        "release_lifecycle_events",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "release_id",
            sa.String(128),
            sa.ForeignKey("core.releases.release_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("from_state", sa.String(64), nullable=True),
        sa.Column("to_state", sa.String(64), nullable=False, index=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False, index=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        schema="governance",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
