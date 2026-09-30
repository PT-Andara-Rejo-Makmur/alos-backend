"""Preserve decision rationale separately from the approval request reason.

Revision ID: 0024_approval_decision_reason
Revises: 0023_identity_access
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_approval_decision_reason"
down_revision = "0023_identity_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_approvals", sa.Column("decision_reason", sa.Text(), nullable=True), schema="core"
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
