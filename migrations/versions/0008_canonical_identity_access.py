"""Evolve identity and session storage for multi-workspace accounts.

Revision ID: 0008_canonical_identity_access
Revises: 0007_runtime_research
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0008_canonical_identity_access"
down_revision = "0007_runtime_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("workspace_key", sa.String(64)), schema="core")
    op.add_column("workspaces", sa.Column("workspace_type", sa.String(32)), schema="core")
    op.add_column("workspaces", sa.Column("organizational_unit_id", sa.String(128)), schema="core")
    op.add_column("workspaces", sa.Column("division_code", sa.String(64)), schema="core")
    op.execute(
        "UPDATE core.workspaces SET workspace_key = "
        "upper(regexp_replace(workspace_id, '[^A-Za-z0-9]+', '_', 'g'))"
    )
    op.execute("UPDATE core.workspaces SET workspace_type = 'BUSINESS'")
    op.alter_column("workspaces", "workspace_key", nullable=False, schema="core")
    op.alter_column("workspaces", "workspace_type", nullable=False, schema="core")
    op.create_unique_constraint(
        "uq_workspaces_organization_key",
        "workspaces",
        ["organization_id", "workspace_key"],
        schema="core",
    )

    now = datetime.now(UTC)
    op.add_column(
        "workspace_memberships",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    op.execute(
        sa.text("UPDATE core.workspace_memberships SET created_at = :now").bindparams(now=now)
    )
    op.alter_column("workspace_memberships", "created_at", nullable=False, schema="core")

    # Legacy columns remain nullable for a safe rolling migration. They are not runtime authority.
    op.alter_column("auth_accounts", "workspace_id", nullable=True, schema="core")
    op.add_column(
        "auth_sessions",
        sa.Column("active_workspace_id", sa.String(128), nullable=True),
        schema="core",
    )
    op.execute("UPDATE core.auth_sessions SET active_workspace_id = workspace_id")
    op.alter_column("auth_sessions", "workspace_id", nullable=True, schema="core")


def downgrade() -> None:
    raise RuntimeError(
        "Canonical identity migration is append-only and cannot be downgraded safely"
    )
