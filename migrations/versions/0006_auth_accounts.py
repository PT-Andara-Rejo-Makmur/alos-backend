"""Add backend-owned auth account tables.

Revision ID: 0006_auth_accounts
Revises: 0005_knowledge_authority
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_auth_accounts"
down_revision = "0005_knowledge_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_accounts",
        sa.Column("account_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False, index=True, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("actor_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["core.auth_accounts.account_id"],
            name="fk_auth_sessions_account_id",
        ),
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
