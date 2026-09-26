"""Add Finance workspace domain persistence.

Revision ID: 0014_finance
Revises: 0013_shared_work
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_finance"
down_revision = "0013_shared_work"
branch_labels = None
depends_on = None


def _scope(schema="core"):
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            index=True,
        ),
    ]


def _dates():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "finance"'))

    op.create_table(
        "bank_accounts",
        *_scope(),
        sa.Column("bank_account_id", sa.String(128), primary_key=True),
        sa.Column("account_name", sa.String(300), nullable=False),
        sa.Column("bank_name", sa.String(200), nullable=False),
        sa.Column("account_number_masked", sa.String(100), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, server_default="IDR"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "bank_transactions",
        *_scope(),
        sa.Column("transaction_id", sa.String(128), primary_key=True),
        sa.Column(
            "bank_account_id",
            sa.String(128),
            sa.ForeignKey("finance.bank_accounts.bank_account_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("transaction_date", sa.Date(), nullable=False, index=True),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False, server_default="IDR"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="POSTED"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "receivables",
        *_scope(),
        sa.Column("receivable_id", sa.String(128), primary_key=True),
        sa.Column("customer_ref", sa.String(128), nullable=True, index=True),
        sa.Column("reference", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True, index=True),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("outstanding_amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "receivable_payments",
        *_scope(),
        sa.Column("payment_id", sa.String(128), primary_key=True),
        sa.Column(
            "receivable_id",
            sa.String(128),
            sa.ForeignKey("finance.receivables.receivable_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="POSTED"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "payables",
        *_scope(),
        sa.Column("payable_id", sa.String(128), primary_key=True),
        sa.Column("vendor_ref", sa.String(128), nullable=True, index=True),
        sa.Column("reference", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True, index=True),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("outstanding_amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "payable_payments",
        *_scope(),
        sa.Column("payment_id", sa.String(128), primary_key=True),
        sa.Column(
            "payable_id",
            sa.String(128),
            sa.ForeignKey("finance.payables.payable_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="POSTED"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "budgets",
        *_scope(),
        sa.Column("budget_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "fiscal_year", "name", name="uq_finance_budget"
        ),
        schema="finance",
    )
    op.create_table(
        "budget_lines",
        *_scope(),
        sa.Column("budget_line_id", sa.String(128), primary_key=True),
        sa.Column(
            "budget_id",
            sa.String(128),
            sa.ForeignKey("finance.budgets.budget_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("cost_center", sa.String(128), nullable=True, index=True),
        sa.Column("account_code", sa.String(128), nullable=False, index=True),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("planned_amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("revised_amount", sa.Numeric(20, 2), nullable=True),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "reconciliations",
        *_scope(),
        sa.Column("reconciliation_id", sa.String(128), primary_key=True),
        sa.Column(
            "bank_account_id",
            sa.String(128),
            sa.ForeignKey("finance.bank_accounts.bank_account_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("reconciled_by", sa.String(128), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "reconciliation_items",
        *_scope(),
        sa.Column("reconciliation_item_id", sa.String(128), primary_key=True),
        sa.Column(
            "reconciliation_id",
            sa.String(128),
            sa.ForeignKey("finance.reconciliations.reconciliation_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "transaction_id",
            sa.String(128),
            sa.ForeignKey("finance.bank_transactions.transaction_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("expected_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("actual_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="UNMATCHED"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "tax_obligations",
        *_scope(),
        sa.Column("tax_obligation_id", sa.String(128), primary_key=True),
        sa.Column("tax_type", sa.String(128), nullable=False, index=True),
        sa.Column("period", sa.String(32), nullable=False, index=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "tax_documents",
        *_scope(),
        sa.Column("tax_document_id", sa.String(128), primary_key=True),
        sa.Column(
            "tax_obligation_id",
            sa.String(128),
            sa.ForeignKey("finance.tax_obligations.tax_obligation_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("document_id", sa.String(128), nullable=True, index=True),
        sa.Column("document_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        *_dates(),
        schema="finance",
    )
    op.create_table(
        "month_closes",
        *_scope(),
        sa.Column("month_close_id", sa.String(128), primary_key=True),
        sa.Column("period", sa.String(16), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(128), nullable=True),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "period", name="uq_finance_month_close"
        ),
        schema="finance",
    )
    op.create_table(
        "month_close_items",
        *_scope(),
        sa.Column("month_close_item_id", sa.String(128), primary_key=True),
        sa.Column(
            "month_close_id",
            sa.String(128),
            sa.ForeignKey("finance.month_closes.month_close_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("item_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="finance",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
