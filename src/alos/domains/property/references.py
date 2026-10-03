"""Property owns Unit visibility; Sales receives only a narrow read port."""

from dataclasses import replace
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.property.records import SPECS
from alos.domains.record_repository import RecordRepository, authorize, conflict
from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.security.errors import PlatformError


class PropertyUnitReferences:
    def __init__(self, repository: RecordRepository, shared_work: SharedWorkService) -> None:
        self.repository = repository
        self.shared_work = shared_work

    async def conditions(self, session: AsyncSession, principal: Principal) -> tuple[Any, ...]:
        table = await self.repository.table(session, "property", "property_units")
        visible = await self.shared_work.visible_project_ids(session, principal)
        return (
            table.c.tenant_id == principal.tenant_id,
            table.c.organization_id == principal.organization_id,
            or_(table.c.workspace_id == principal.workspace_id, table.c.project_id.in_(visible)),
        )

    async def validate(
        self, session: AsyncSession, principal: Principal, identity: str, *, lock: bool = False
    ) -> dict[str, Any]:
        table = await self.repository.table(session, "property", "property_units")
        query = select(table).where(
            *await self.conditions(session, principal), table.c.property_unit_id == identity
        )
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query)).mappings().first()
        if row is None:
            raise PlatformError(
                "UNIT_NOT_VISIBLE", "Property unit is not visible.", status_code=404
            )
        return dict(row)

    async def synchronize_booking(
        self,
        session: AsyncSession,
        principal: Principal,
        unit_id: str,
        booking_id: str,
        *,
        sold: bool = False,
    ) -> None:
        unit = await self.validate(session, principal, unit_id, lock=True)
        if sold:
            if (
                unit["status"] not in {"RESERVED", "SOLD"}
                or unit["reservation_booking_id"] != booking_id
            ):
                raise conflict("Closing membutuhkan reservasi unit dari Booking yang sama.")
        elif unit["status"] != "AVAILABLE":
            raise conflict("Unit tidak tersedia untuk konfirmasi Booking.")
        await self.repository.write(
            session,
            "property",
            SPECS["property_units"],
            replace(principal, workspace_id=unit["workspace_id"]),
            {"status": "SOLD" if sold else "RESERVED", "reservation_booking_id": booking_id},
            unit_id,
            operation="booking_sale" if sold else "booking_reservation",
        )

    async def listing(self, principal: Principal, limit: int, offset: int) -> dict[str, Any]:
        authorize(principal, "sales", "read")
        async with self.repository.factory() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await self.repository.workspace(session, principal)
            table = await self.repository.table(session, "property", "property_units")
            where = await self.conditions(session, principal)
            count, stamp = (
                await session.execute(
                    select(func.count(), func.max(table.c.updated_at)).where(*where)
                )
            ).one()
            rows = (
                (
                    await session.execute(
                        select(
                            table.c.property_unit_id,
                            table.c.unit_code,
                            table.c.status,
                            table.c.project_id,
                        )
                        .where(*where)
                        .order_by(table.c.unit_code, table.c.property_unit_id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "items": [dict(row) for row in rows],
                "total": count,
                "source": self.repository.source("property", count, stamp),
            }
