"""Record financing and akad coordination against the canonical Booking."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_sales_financing"
down_revision = "0044_payment_bank_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financing_contexts",
        sa.Column("financing_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column(
            "booking_id",
            sa.String(128),
            sa.ForeignKey("sales.bookings.booking_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("payment_method", sa.String(24), nullable=False),
        sa.Column("bank_reference", sa.String(300)),
        sa.Column("required_document_notes", sa.Text()),
        sa.Column("document_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("sp3k_reference", sa.String(300)),
        sa.Column("sp3k_on", sa.Date()),
        sa.Column("akad_on", sa.Date()),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column(
            "responsible_actor_id",
            sa.String(128),
            sa.ForeignKey("core.actors.actor_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="COLLECTING"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "payment_method IN ('CASH','CASH_INSTALLMENT','KPR')", name="ck_sales_financing_method"
        ),
        sa.CheckConstraint(
            "status IN ('COLLECTING','BANK_REVIEW','READY',"
            "'SP3K_ISSUED','AKAD_COMPLETED','CANCELLED')",
            name="ck_sales_financing_status",
        ),
        schema="sales",
    )
    op.create_index(
        "ix_sales_financing_scope",
        "financing_contexts",
        ["tenant_id", "organization_id", "workspace_id", "status"],
        schema="sales",
    )


def downgrade() -> None:
    raise RuntimeError("Retain financing evidence and Booking lineage; use an additive correction.")
