"""Link onboarding to existing GA facility evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0040_onboarding_facilities"
down_revision = "0039_document_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "onboardings", sa.Column("facility_request_id", sa.String(128), nullable=True), schema="hr"
    )
    op.create_foreign_key(
        "fk_onboarding_facility_request",
        "onboardings",
        "facility_requests",
        ["facility_request_id"],
        ["facility_request_id"],
        source_schema="hr",
        referent_schema="hr",
    )
    op.create_index(
        "ix_onboarding_facility_request", "onboardings", ["facility_request_id"], schema="hr"
    )


def downgrade() -> None:
    raise RuntimeError("Retain onboarding evidence; use an additive correction migration.")
