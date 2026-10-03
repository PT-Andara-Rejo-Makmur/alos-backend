"""A bank transaction settles one canonical payment record."""

import sqlalchemy as sa
from alembic import op

revision = "0044_payment_bank_lineage"
down_revision = "0043_project_responsibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_business_bank_settlement",
        "business_record_links",
        ["tenant_id", "organization_id", "target_type", "target_id", "relation"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("relation = 'SETTLED_BY'"),
    )


def downgrade() -> None:
    raise RuntimeError("Retain payment settlement integrity; use an additive correction migration.")
