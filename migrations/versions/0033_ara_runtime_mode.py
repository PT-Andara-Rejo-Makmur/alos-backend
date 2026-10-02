"""Persist the actual ARA execution route without rewriting historical test runs."""

import sqlalchemy as sa
from alembic import op

revision = "0033_ara_runtime_mode"
down_revision = "0032_ara_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ara_runs",
        sa.Column(
            "runtime_mode", sa.String(32), nullable=False, server_default="DETERMINISTIC_TEST"
        ),
        schema="core",
    )


def downgrade() -> None:
    op.drop_column("ara_runs", "runtime_mode", schema="core")
