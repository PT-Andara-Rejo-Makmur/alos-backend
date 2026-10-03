"""Lineage shares references through an owned record without granting foreign record access."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, or_, select

from alos.domains.finance.records import SPECS as FINANCE
from alos.domains.hr.records import SPECS as HR
from alos.domains.legal.records import SPECS as LEGAL
from alos.domains.property.records import SPECS as PROPERTY
from alos.domains.record_repository import RecordRepository, authorize, conflict
from alos.domains.sales.records import SPECS as SALES
from alos.identity import Principal
from alos.processes.authority import revalidate

RECORD_TYPES = {
    "PROPERTY_PAYMENT_CERTIFICATE": ("property", PROPERTY["payment_certificates"]),
    "PROPERTY_CHANGE_ORDER": ("property", PROPERTY["change_orders"]),
    "FINANCE_PAYABLE": ("finance", FINANCE["payables"]),
    "FINANCE_PAYABLE_PAYMENT": ("finance", FINANCE["payable_payments"]),
    "FINANCE_BANK_TRANSACTION": ("finance", FINANCE["bank_transactions"]),
    "LEGAL_CONTRACT": ("legal", LEGAL["contracts"]),
    "SALES_BOOKING": ("sales", SALES["bookings"]),
    "SALES_CLOSING": ("sales", SALES["closings"]),
    "HR_EMPLOYEE": ("hr", HR["employees"]),
    "HR_ONBOARDING": ("hr", HR["onboardings"]),
    "HR_EMPLOYMENT_CONTRACT": ("hr", HR["employment_contracts"]),
}


async def relationships(
    repository: RecordRepository, principal: Principal, record_type: str, identity: str
) -> dict[str, Any]:
    if record_type not in RECORD_TYPES:
        raise conflict("Jenis hubungan bisnis tidak tersedia.")
    domain, spec = RECORD_TYPES[record_type]
    authorize(principal, domain, "read")
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        await repository.row(session, domain, spec, principal, identity)
        table = await repository.table(session, "core", "business_record_links")
        rows = (
            (
                await session.execute(
                    select(table)
                    .where(
                        table.c.tenant_id == principal.tenant_id,
                        table.c.organization_id == principal.organization_id,
                        or_(
                            and_(
                                table.c.source_type == record_type,
                                table.c.source_id == identity,
                                table.c.source_workspace_id == principal.workspace_id,
                            ),
                            and_(
                                table.c.target_type == record_type,
                                table.c.target_id == identity,
                                table.c.target_workspace_id == principal.workspace_id,
                            ),
                        ),
                    )
                    .order_by(table.c.created_at)
                    .limit(100)
                )
            )
            .mappings()
            .all()
        )
        return {"items": jsonable_encoder([dict(row) for row in rows])}
