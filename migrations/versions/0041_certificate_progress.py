"""Keep verified construction progress traceable from payment certificates."""

import sqlalchemy as sa
from alembic import op

revision = "0041_certificate_progress"
down_revision = "0040_onboarding_facilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_certificates",
        sa.Column("construction_update_id", sa.String(128), nullable=True),
        schema="property",
    )
    op.create_foreign_key(
        "fk_certificate_construction_update",
        "payment_certificates",
        "construction_updates",
        ["construction_update_id"],
        ["construction_update_id"],
        source_schema="property",
        referent_schema="property",
    )
    op.create_index(
        "ix_certificate_construction_update",
        "payment_certificates",
        ["construction_update_id"],
        schema="property",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Retain verified construction evidence; use an additive correction migration."
    )
