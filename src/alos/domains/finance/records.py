"""Curated migration-owned resources and conservative internal lifecycle."""

from dataclasses import replace

from alos.domains.record_repository import RecordSpec
from alos.identity import Principal


def internal_decision_visible(status: str, principal: Principal) -> bool:
    return status not in {"APPROVED", "ACTIVE", "CLOSED"} or "DIVISION_LEAD" in principal.roles


SPECS = {
    "bank_accounts": RecordSpec(
        "bank_accounts",
        "bank_account_id",
        "FinanceBankAccount",
        "ACTIVE",
        {"ACTIVE": ("INACTIVE",), "INACTIVE": ("ACTIVE",)},
        frozenset(["account_name", "bank_name", "account_number_masked", "currency"]),
        frozenset(["account_name", "bank_name", "account_number_masked"]),
        False,
    ),
    "bank_transactions": RecordSpec(
        "bank_transactions",
        "transaction_id",
        "FinanceBankTransaction",
        "POSTED",
        {},
        frozenset(
            [
                "bank_account_id",
                "transaction_date",
                "reference",
                "description",
                "direction",
                "amount",
                "currency",
            ]
        ),
        frozenset([]),
        True,
    ),
    "receivables": RecordSpec(
        "receivables",
        "receivable_id",
        "FinanceReceivable",
        "OPEN",
        {"OPEN": ("CANCELLED",)},
        frozenset(["customer_ref", "reference", "description", "due_date", "amount"]),
        frozenset(["description", "due_date"]),
        False,
    ),
    "receivable_payments": RecordSpec(
        "receivable_payments",
        "payment_id",
        "FinanceReceivablePayment",
        "POSTED",
        {},
        frozenset(["receivable_id", "payment_date", "amount", "reference"]),
        frozenset([]),
        True,
    ),
    "payables": RecordSpec(
        "payables",
        "payable_id",
        "FinancePayable",
        "OPEN",
        {"OPEN": ("CANCELLED",)},
        frozenset(["vendor_ref", "reference", "description", "due_date", "amount"]),
        frozenset(["description", "due_date"]),
        False,
    ),
    "payable_payments": RecordSpec(
        "payable_payments",
        "payment_id",
        "FinancePayablePayment",
        "POSTED",
        {},
        frozenset(["payable_id", "payment_date", "amount", "reference"]),
        frozenset([]),
        True,
    ),
    "budgets": RecordSpec(
        "budgets",
        "budget_id",
        "FinanceBudget",
        "DRAFT",
        {
            "DRAFT": ("UNDER_REVIEW",),
            "UNDER_REVIEW": ("DRAFT", "APPROVED"),
            "APPROVED": ("ACTIVE",),
            "ACTIVE": ("CLOSED",),
        },
        frozenset(["name", "fiscal_year"]),
        frozenset(["name", "fiscal_year"]),
        False,
    ),
    "budget_lines": RecordSpec(
        "budget_lines",
        "budget_line_id",
        "FinanceBudgetLine",
        None,
        {},
        frozenset(
            [
                "budget_id",
                "cost_center",
                "account_code",
                "period",
                "planned_amount",
                "revised_amount",
            ]
        ),
        frozenset(["cost_center", "account_code", "planned_amount", "revised_amount"]),
        False,
    ),
    "reconciliations": RecordSpec(
        "reconciliations",
        "reconciliation_id",
        "FinanceReconciliation",
        "OPEN",
        {"OPEN": ("CLOSED",)},
        frozenset(["bank_account_id", "period_start", "period_end"]),
        frozenset(),
        False,
    ),
    "reconciliation_items": RecordSpec(
        "reconciliation_items",
        "reconciliation_item_id",
        "FinanceReconciliationItem",
        "UNMATCHED",
        {"UNMATCHED": ("MATCHED",)},
        frozenset(["reconciliation_id", "transaction_id", "expected_amount", "actual_amount"]),
        frozenset(["expected_amount", "actual_amount"]),
        False,
    ),
    "tax_obligations": RecordSpec(
        "tax_obligations",
        "tax_obligation_id",
        "FinanceTaxObligation",
        "OPEN",
        {"OPEN": ("CLOSED",)},
        frozenset(["tax_type", "period", "due_date", "amount"]),
        frozenset(["tax_type", "due_date"]),
        False,
    ),
    "tax_documents": RecordSpec(
        "tax_documents",
        "tax_document_id",
        "FinanceTaxDocument",
        "DRAFT",
        {"DRAFT": ("RECORDED",)},
        frozenset(["tax_obligation_id", "document_id", "document_type"]),
        frozenset(["document_type"]),
        False,
    ),
    "month_closes": RecordSpec(
        "month_closes",
        "month_close_id",
        "FinanceMonthClose",
        "OPEN",
        {"OPEN": ("CLOSED",)},
        frozenset(["period"]),
        frozenset(),
        False,
    ),
    "month_close_items": RecordSpec(
        "month_close_items",
        "month_close_item_id",
        "FinanceMonthCloseItem",
        "OPEN",
        {"OPEN": ("COMPLETED",)},
        frozenset(["month_close_id", "item_type", "notes"]),
        frozenset(["item_type", "notes"]),
        False,
    ),
}

for resource in ("budgets", "reconciliations", "tax_obligations", "month_closes"):
    SPECS[resource] = replace(SPECS[resource], transition_authorized=internal_decision_visible)
