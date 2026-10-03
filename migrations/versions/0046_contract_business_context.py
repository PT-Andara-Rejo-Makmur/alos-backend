"""Record contract impact without granting Legal ownership of business decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0046_contract_business_context"
down_revision = "0045_sales_financing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "change_orders",
        sa.Column(
            "contract_change_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        schema="property",
    )
    op.add_column(
        "change_orders",
        sa.Column("implementation_notes", sa.Text(), nullable=True),
        schema="property",
    )
    op.add_column(
        "change_orders",
        sa.Column("implemented_at", sa.DateTime(timezone=True), nullable=True),
        schema="property",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Retain the business reason for contract review; use an additive correction."
    )
