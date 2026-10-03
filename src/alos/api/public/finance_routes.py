"""Named schema-validated HTTP adapters; canonical schemas own every field."""

from fastapi import Request
from fastapi.responses import JSONResponse

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes, response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.finance.origins import link_payment_transaction, payable_from_certificate
from alos.domains.finance.records import SPECS

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class FinanceBankAccountCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBankAccountCreateRequest"


MODELS[("bank_accounts", "create")] = FinanceBankAccountCreateRequest


class FinanceBankAccountUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBankAccountUpdateRequest"


MODELS[("bank_accounts", "update")] = FinanceBankAccountUpdateRequest


class FinanceBankAccountTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBankAccountTransitionRequest"


MODELS[("bank_accounts", "transition")] = FinanceBankAccountTransitionRequest


class FinanceBankTransactionCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBankTransactionCreateRequest"


MODELS[("bank_transactions", "create")] = FinanceBankTransactionCreateRequest


class FinanceBankTransactionUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBankTransactionUpdateRequest"


MODELS[("bank_transactions", "update")] = FinanceBankTransactionUpdateRequest


class FinanceBankTransactionTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBankTransactionTransitionRequest"


MODELS[("bank_transactions", "transition")] = FinanceBankTransactionTransitionRequest


class FinanceReceivableCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReceivableCreateRequest"


MODELS[("receivables", "create")] = FinanceReceivableCreateRequest


class FinanceReceivableUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReceivableUpdateRequest"


MODELS[("receivables", "update")] = FinanceReceivableUpdateRequest


class FinanceReceivableTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReceivableTransitionRequest"


MODELS[("receivables", "transition")] = FinanceReceivableTransitionRequest


class FinanceReceivablePaymentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReceivablePaymentCreateRequest"


MODELS[("receivable_payments", "create")] = FinanceReceivablePaymentCreateRequest


class FinanceReceivablePaymentUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReceivablePaymentUpdateRequest"


MODELS[("receivable_payments", "update")] = FinanceReceivablePaymentUpdateRequest


class FinanceReceivablePaymentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReceivablePaymentTransitionRequest"


MODELS[("receivable_payments", "transition")] = FinanceReceivablePaymentTransitionRequest


class FinancePayableCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayableCreateRequest"


MODELS[("payables", "create")] = FinancePayableCreateRequest


class FinancePayableUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayableUpdateRequest"


MODELS[("payables", "update")] = FinancePayableUpdateRequest


class FinancePayableTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayableTransitionRequest"


MODELS[("payables", "transition")] = FinancePayableTransitionRequest


class FinancePayablePaymentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayablePaymentCreateRequest"


MODELS[("payable_payments", "create")] = FinancePayablePaymentCreateRequest


class FinancePayablePaymentUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayablePaymentUpdateRequest"


MODELS[("payable_payments", "update")] = FinancePayablePaymentUpdateRequest


class FinancePayablePaymentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayablePaymentTransitionRequest"


MODELS[("payable_payments", "transition")] = FinancePayablePaymentTransitionRequest


class FinanceBudgetCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBudgetCreateRequest"


MODELS[("budgets", "create")] = FinanceBudgetCreateRequest


class FinanceBudgetUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBudgetUpdateRequest"


MODELS[("budgets", "update")] = FinanceBudgetUpdateRequest


class FinanceBudgetTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBudgetTransitionRequest"


MODELS[("budgets", "transition")] = FinanceBudgetTransitionRequest


class FinanceBudgetLineCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBudgetLineCreateRequest"


MODELS[("budget_lines", "create")] = FinanceBudgetLineCreateRequest


class FinanceBudgetLineUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceBudgetLineUpdateRequest"


MODELS[("budget_lines", "update")] = FinanceBudgetLineUpdateRequest


class FinanceReconciliationCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReconciliationCreateRequest"


MODELS[("reconciliations", "create")] = FinanceReconciliationCreateRequest


class FinanceReconciliationUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReconciliationUpdateRequest"


MODELS[("reconciliations", "update")] = FinanceReconciliationUpdateRequest


class FinanceReconciliationTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReconciliationTransitionRequest"


MODELS[("reconciliations", "transition")] = FinanceReconciliationTransitionRequest


class FinanceReconciliationItemCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReconciliationItemCreateRequest"


MODELS[("reconciliation_items", "create")] = FinanceReconciliationItemCreateRequest


class FinanceReconciliationItemUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReconciliationItemUpdateRequest"


MODELS[("reconciliation_items", "update")] = FinanceReconciliationItemUpdateRequest


class FinanceReconciliationItemTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceReconciliationItemTransitionRequest"


MODELS[("reconciliation_items", "transition")] = FinanceReconciliationItemTransitionRequest


class FinanceTaxObligationCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceTaxObligationCreateRequest"


MODELS[("tax_obligations", "create")] = FinanceTaxObligationCreateRequest


class FinanceTaxObligationUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceTaxObligationUpdateRequest"


MODELS[("tax_obligations", "update")] = FinanceTaxObligationUpdateRequest


class FinanceTaxObligationTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceTaxObligationTransitionRequest"


MODELS[("tax_obligations", "transition")] = FinanceTaxObligationTransitionRequest


class FinanceTaxDocumentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceTaxDocumentCreateRequest"


MODELS[("tax_documents", "create")] = FinanceTaxDocumentCreateRequest


class FinanceTaxDocumentUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceTaxDocumentUpdateRequest"


MODELS[("tax_documents", "update")] = FinanceTaxDocumentUpdateRequest


class FinanceTaxDocumentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceTaxDocumentTransitionRequest"


MODELS[("tax_documents", "transition")] = FinanceTaxDocumentTransitionRequest


class FinanceMonthCloseCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceMonthCloseCreateRequest"


MODELS[("month_closes", "create")] = FinanceMonthCloseCreateRequest


class FinanceMonthCloseUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceMonthCloseUpdateRequest"


MODELS[("month_closes", "update")] = FinanceMonthCloseUpdateRequest


class FinanceMonthCloseTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceMonthCloseTransitionRequest"


MODELS[("month_closes", "transition")] = FinanceMonthCloseTransitionRequest


class FinanceMonthCloseItemCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceMonthCloseItemCreateRequest"


MODELS[("month_close_items", "create")] = FinanceMonthCloseItemCreateRequest


class FinanceMonthCloseItemUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceMonthCloseItemUpdateRequest"


MODELS[("month_close_items", "update")] = FinanceMonthCloseItemUpdateRequest


class FinanceMonthCloseItemTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinanceMonthCloseItemTransitionRequest"


MODELS[("month_close_items", "transition")] = FinanceMonthCloseItemTransitionRequest

router = register_record_routes("finance", SPECS, MODELS)


class FinancePaymentTransactionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePaymentTransactionRequest"


@router.post("/payable-payments/{payment_id}/bank-transaction")
async def associate_payment_transaction(
    payment_id: str,
    payload: FinancePaymentTransactionRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> JSONResponse:
    values = payload.validated(contracts)
    result = await link_payment_transaction(
        request.app.state.finance_service,
        principal,
        payment_id,
        values["transaction_id"],
        values["reason"],
    )
    return response(
        contracts,
        "https://schemas.alos.dev/v1/business/business-contracts.schema.json#/$defs/BusinessRelationship",
        result,
    )


class FinancePayableFromCertificateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayableFromCertificateRequest"


@router.post("/payment-certificates/{process_id}/payable")
async def create_certificate_payable(
    process_id: str,
    payload: FinancePayableFromCertificateRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> JSONResponse:
    result = await payable_from_certificate(
        request.app.state.finance_service,
        request.app.state.process_service,
        principal,
        process_id,
        payload.validated(contracts),
    )
    return response(
        contracts,
        "https://schemas.alos.dev/v1/finance/finance-contracts.schema.json#/$defs/FinancePayableProjection",
        result,
    )
