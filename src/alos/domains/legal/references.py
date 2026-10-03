"""A contract reference is scoped to the owned submission, never a Legal read grant."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, conflict
from alos.identity import Principal


async def validate_change_contract(
    repository: RecordRepository,
    session: AsyncSession,
    principal: Principal,
    subject_id: str | None,
    contract_id: str,
) -> None:
    contracts = await repository.table(session, "legal", "contracts")
    links = await repository.table(session, "core", "business_record_links")
    contract = (
        (
            await session.execute(
                select(contracts.c.contract_id, contracts.c.workspace_id).where(
                    contracts.c.tenant_id == principal.tenant_id,
                    contracts.c.organization_id == principal.organization_id,
                    contracts.c.contract_id == contract_id,
                )
            )
        )
        .mappings()
        .first()
    )
    if contract and contract["workspace_id"] == principal.workspace_id:
        return
    linked = await session.scalar(
        select(links.c.link_id).where(
            links.c.tenant_id == principal.tenant_id,
            links.c.organization_id == principal.organization_id,
            links.c.source_workspace_id == principal.workspace_id,
            links.c.source_type == "PROPERTY_CHANGE_ORDER",
            links.c.source_id == subject_id,
            links.c.target_type == "LEGAL_CONTRACT",
            links.c.target_id == contract_id,
            links.c.relation == "GOVERNED_BY",
        )
    )
    if not contract or not linked:
        raise conflict("Kontrak belum dihubungkan oleh Legal untuk pengajuan ini.")
