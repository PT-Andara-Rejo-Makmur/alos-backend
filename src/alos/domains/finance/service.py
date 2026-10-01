"""Finance owns its lifecycle, references and business validation."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.finance.records import SPECS
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal
from alos.security.errors import PlatformError


class FinanceService:
    def __init__(self, repository: RecordRepository, **ports: Any) -> None:
        self.repository = repository
        self.ports = ports

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "finance", "read")
        return await self.repository.listing("finance", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "finance", "read")
        return await self.repository.detail("finance", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "finance", "read", executive=executive)
        return await self.repository.summary("finance", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "finance", "write")
        return await self.repository.mutate(
            "finance",
            SPECS[resource],
            principal,
            payload,
            identity,
            operation,
            self._rule,
            before_lock=self._prepare,
        )

    async def _prepare(
        self,
        session: AsyncSession,
        spec: RecordSpec,
        principal: Principal,
        values: dict[str, Any],
        old: dict[str, Any] | None,
        operation: str,
    ) -> None:
        """Acquire period locks before record locks, in a deterministic order."""
        del operation
        data = {**(old or {}), **values}
        periods: set[str] = set()
        for name, field in (
            ("bank_transactions", "transaction_date"),
            ("receivable_payments", "payment_date"),
            ("payable_payments", "payment_date"),
        ):
            if spec.table == name:
                periods.add(data[field].strftime("%Y-%m"))
        if spec.table in {"receivables", "payables", "tax_obligations"}:
            periods.add(data.get("created_at", datetime.now(UTC)).strftime("%Y-%m"))
        if spec.table in {"budget_lines", "tax_obligations", "month_closes"}:
            periods.add(data["period"])
        parent = None
        for name, resource, field in (
            ("reconciliation_items", "reconciliations", "reconciliation_id"),
            ("tax_documents", "tax_obligations", "tax_obligation_id"),
            ("month_close_items", "month_closes", "month_close_id"),
        ):
            if spec.table == name:
                parent = await self.repository.row(
                    session, "finance", SPECS[resource], principal, data[field]
                )
                if "period" in parent:
                    periods.add(parent["period"])
        if spec.table == "reconciliations" or spec.table == "reconciliation_items":
            period_data = parent if spec.table == "reconciliation_items" else data
            assert period_data is not None
            periods.update(self._periods(period_data["period_start"], period_data["period_end"]))
        for period in sorted(periods):
            await self._open_period(session, principal, period)

    @staticmethod
    def _periods(start: date, end: date) -> tuple[str, ...]:
        if start > end or (end.year - start.year) * 12 + end.month - start.month > 35:
            raise conflict("Reconciliation period is invalid or exceeds thirty-six months.")
        periods = []
        current = date(start.year, start.month, 1)
        while current <= end:
            periods.append(current.strftime("%Y-%m"))
            current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        return tuple(periods)

    async def _rule(
        self,
        session: AsyncSession,
        spec: RecordSpec,
        principal: Principal,
        values: dict[str, Any],
        old: dict[str, Any] | None,
        operation: str,
    ) -> None:
        data = {**(old or {}), **values}
        name = spec.table
        if old and any(
            field in values and values[field] != old[field]
            for field in ("period", "period_start", "period_end")
            if field in old
        ):
            raise conflict("Recorded financial periods are immutable.")
        if (
            old
            and old.get("status")
            in {"CLOSED", "CANCELLED", "PAID", "RECORDED", "MATCHED", "INACTIVE"}
            and operation == "update"
        ):
            raise conflict("Historical or terminal Finance records cannot be edited.")
        if old and name in {"receivables", "payables", "tax_obligations"}:
            await self._open_period(session, principal, old["created_at"].strftime("%Y-%m"))
        if name == "bank_transactions":
            await self._open_period(session, principal, data["transaction_date"].strftime("%Y-%m"))
            account = await self.repository.row(
                session,
                "finance",
                SPECS["bank_accounts"],
                principal,
                data["bank_account_id"],
                lock=True,
            )
            if account["status"] != "ACTIVE" or data.get("currency", "IDR") != account["currency"]:
                raise conflict("Bank account status or currency does not match.")
            if data["amount"] <= 0:
                raise conflict("Transaction amount must be positive.")
        if name in {"receivables", "payables"}:
            if old is None:
                if data["amount"] <= 0:
                    raise conflict("Invoice amount must be positive.")
                values["outstanding_amount"] = data["amount"]
                await self._open_period(session, principal, datetime.now(UTC).strftime("%Y-%m"))
            if values.get("status") == "CANCELLED" and data["outstanding_amount"] != data["amount"]:
                raise conflict("An invoice with payments cannot be cancelled.")
        if name in {"receivable_payments", "payable_payments"}:
            await self._payment(session, principal, spec, data)
        if name == "budgets":
            if operation == "update" and old and old["status"] != "DRAFT":
                raise conflict("Reviewed budgets are immutable.")
        if name == "budget_lines":
            await self._open_period(session, principal, data["period"])
            budget = await self.repository.row(
                session, "finance", SPECS["budgets"], principal, data["budget_id"], lock=True
            )
            if budget["status"] != "DRAFT" or int(data["period"][:4]) != budget["fiscal_year"]:
                raise conflict("Budget period or lifecycle is invalid.")
        if name == "reconciliations":
            account = await self.repository.row(
                session,
                "finance",
                SPECS["bank_accounts"],
                principal,
                data["bank_account_id"],
                lock=True,
            )
            if account["status"] != "ACTIVE" or data["period_start"] > data["period_end"]:
                raise conflict("Reconciliation account or date range is invalid.")
            if operation == "update":
                items = await self._children(
                    session,
                    principal,
                    "reconciliation_items",
                    "reconciliation_id",
                    data["reconciliation_id"],
                )
                if items:
                    raise conflict("Reconciliation periods with recorded items cannot be edited.")
            if values.get("status") == "CLOSED":
                self._lead(principal)
                items = await self._children(
                    session,
                    principal,
                    "reconciliation_items",
                    "reconciliation_id",
                    data["reconciliation_id"],
                )
                if not items or any(item["status"] != "MATCHED" for item in items):
                    raise conflict("Unresolved reconciliation items prevent closing.")
                values.update(reconciled_by=principal.actor_id, reconciled_at=datetime.now(UTC))
        if name == "reconciliation_items":
            parent = await self.repository.row(
                session,
                "finance",
                SPECS["reconciliations"],
                principal,
                data["reconciliation_id"],
                lock=True,
            )
            if parent["status"] != "OPEN":
                raise conflict("Reconciliation is closed.")
            transaction = None
            if data.get("transaction_id"):
                transaction = await self.repository.row(
                    session,
                    "finance",
                    SPECS["bank_transactions"],
                    principal,
                    data["transaction_id"],
                )
                if (
                    transaction["bank_account_id"] != parent["bank_account_id"]
                    or not parent["period_start"]
                    <= transaction["transaction_date"]
                    <= parent["period_end"]
                ):
                    raise conflict("Transaction does not belong to this reconciliation.")
                if (
                    data.get("actual_amount") is not None
                    and data["actual_amount"] != transaction["amount"]
                ):
                    raise conflict("Actual amount must match the recorded transaction.")
                if old is None:
                    table = await self.repository.table(session, "finance", "reconciliation_items")
                    duplicate = await session.scalar(
                        select(table.c.reconciliation_item_id)
                        .where(
                            *self.repository.scope(table, principal),
                            table.c.reconciliation_id == data["reconciliation_id"],
                            table.c.transaction_id == data["transaction_id"],
                        )
                        .limit(1)
                    )
                    if duplicate is not None:
                        raise conflict("Transaction is already in this reconciliation.")
            if values.get("status") == "MATCHED" and (
                transaction is None
                or data.get("expected_amount") is None
                or data.get("actual_amount") is None
                or data["expected_amount"] != data["actual_amount"]
            ):
                raise conflict("Matching requires exact expected and recorded amounts.")
        if name == "tax_documents":
            parent = await self.repository.row(
                session,
                "finance",
                SPECS["tax_obligations"],
                principal,
                data["tax_obligation_id"],
                lock=True,
            )
            await self._open_period(session, principal, parent["period"])
            if parent["status"] != "OPEN":
                raise conflict("Tax obligation is closed.")
            if data.get("document_id"):
                await self.ports["work"].validate_document_reference(
                    session, principal, data["document_id"]
                )
            if values.get("status") == "RECORDED" and not data.get("document_id"):
                raise conflict("Recording requires a canonical document.")
        if name == "tax_obligations":
            await self._open_period(session, principal, data["period"])
            if values.get("status") == "CLOSED":
                self._lead(principal)
                documents = await self._children(
                    session,
                    principal,
                    "tax_documents",
                    "tax_obligation_id",
                    data["tax_obligation_id"],
                )
                if (
                    data.get("amount") is None
                    or not data.get("due_date")
                    or not documents
                    or any(doc["status"] != "RECORDED" for doc in documents)
                ):
                    raise conflict(
                        "Internal tax closure requires amount, due date and recorded documents."
                    )
        if name == "month_closes":
            await self._open_period(session, principal, data["period"])
            if old is None:
                values["opened_at"] = datetime.now(UTC)
            elif operation == "update":
                raise conflict("Month close period cannot be changed.")
        if name == "month_close_items":
            parent = await self.repository.row(
                session,
                "finance",
                SPECS["month_closes"],
                principal,
                data["month_close_id"],
                lock=True,
            )
            await self._open_period(session, principal, parent["period"])
            if parent["status"] != "OPEN":
                raise conflict("Month is closed.")

    @staticmethod
    def _lead(principal: Principal) -> None:
        if "DIVISION_LEAD" not in principal.roles:
            raise PlatformError(
                "FINANCE_DECISION_DENIED",
                "This internal governed decision requires a division lead.",
                status_code=403,
            )

    async def _open_period(self, session: AsyncSession, principal: Principal, period: str) -> None:
        # The shared lock serializes period closing with all affected financial writes.
        key = ":".join(
            (principal.tenant_id, principal.organization_id, principal.workspace_id, period)
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"), {"scope": key}
        )
        table = await self.repository.table(session, "finance", "month_closes")
        closed = await session.scalar(
            select(table.c.month_close_id).where(
                *self.repository.scope(table, principal),
                table.c.period == period,
                table.c.status == "CLOSED",
            )
        )
        if closed is not None:
            raise conflict("The financial period is closed.")

    async def _children(
        self,
        session: AsyncSession,
        principal: Principal,
        resource: str,
        parent_field: str,
        identity: str,
    ) -> list[dict[str, Any]]:
        table = await self.repository.table(session, "finance", resource)
        return [
            dict(row)
            for row in (
                await session.execute(
                    select(table).where(
                        *self.repository.scope(table, principal), table.c[parent_field] == identity
                    )
                )
            )
            .mappings()
            .all()
        ]

    async def _payment(
        self, session: AsyncSession, principal: Principal, spec: RecordSpec, data: dict[str, Any]
    ) -> None:
        await self._open_period(session, principal, data["payment_date"].strftime("%Y-%m"))
        resource = "receivables" if spec.table == "receivable_payments" else "payables"
        parent_spec = SPECS[resource]
        identity = data[parent_spec.identifier]
        parent = await self.repository.row(
            session, "finance", parent_spec, principal, identity, lock=True
        )
        if (
            parent["status"] != "OPEN"
            or data["amount"] <= Decimal(0)
            or data["amount"] > parent["outstanding_amount"]
        ):
            raise conflict("Payment exceeds outstanding or invoice is not open.")
        if not data.get("reference"):
            raise conflict("A payment reference is required.")
        history = await self._children(
            session, principal, spec.table, parent_spec.identifier, identity
        )
        if any(item["reference"] == data["reference"] for item in history):
            raise conflict("Duplicate payment reference.")
        remaining = parent["outstanding_amount"] - data["amount"]
        await self.repository.write(
            session,
            "finance",
            parent_spec,
            principal,
            {"outstanding_amount": remaining, "status": "PAID" if remaining == 0 else "OPEN"},
            identity,
            operation="payment_applied",
        )
