"""Add password reset challenges table for governed self-service password recovery.

Revision ID: 0028_password_reset_challenges
Revises: 0027_task_dependencies
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_password_reset_challenges"
down_revision = "0027_task_dependencies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_challenges",
        sa.Column("challenge_id", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["core.auth_accounts.account_id"],
            name="fk_password_reset_challenge_account",
        ),
        schema="core",
    )
    op.create_index(
        "ix_password_reset_challenges_account_id",
        "password_reset_challenges",
        ["account_id"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
