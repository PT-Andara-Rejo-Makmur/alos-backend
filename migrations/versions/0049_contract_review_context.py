"""Record contract review needs, supporting documents and requested Director direction."""

import sqlalchemy as sa
from alembic import op

revision = "0049_contract_review_context"
down_revision = "0048_capability_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("change_orders", "payment_certificates"):
        op.add_column(
            name,
            sa.Column(
                "document_id",
                sa.String(128),
                nullable=True,
            ),
            schema="property",
        )
        op.create_foreign_key(
            f"fk_{name}_supporting_document",
            name,
            "documents",
            ["tenant_id", "workspace_id", "document_id"],
            ["tenant_id", "workspace_id", "document_id"],
            source_schema="property",
            referent_schema="core",
        )
    op.add_column(
        "employment_contracts",
        sa.Column("legal_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="hr",
    )
    op.add_column(
        "business_processes", sa.Column("direction_reason", sa.Text(), nullable=True), schema="core"
    )


def downgrade() -> None:
    raise RuntimeError("Preserve review context and direction reasons; use additive corrections.")
