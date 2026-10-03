"""Property owns its lifecycle, references and business validation."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.property.records import SPECS
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.governance.material_approvals import subject_snapshot
from alos.identity import Principal
from alos.processes.authority import revalidate
from alos.processes.guard import require_reviews
from alos.security.errors import PlatformError


class PropertyService:
    def __init__(self, repository: RecordRepository, **ports: Any) -> None:
        self.repository = repository
        self.ports = ports

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "property", "read")
        return await self.repository.listing("property", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "property", "read")
        return await self.repository.detail("property", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "property", "read", executive=executive)
        return await self.repository.summary("property", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "property", "write")
        return await self.repository.mutate(
            "property",
            SPECS[resource],
            principal,
            payload,
            identity,
            operation,
            self._rule,
            approvals=self.ports.get("work"),
        )

    async def implement_change_order(
        self, principal: Principal, identity: str, reason: str
    ) -> dict[str, Any]:
        authorize(principal, "property", "write")
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            spec = SPECS["change_orders"]
            record = await self.repository.row(
                session, "property", spec, principal, identity, lock=True
            )
            if record["status"] == "IMPLEMENTED":
                return self.repository.project(spec, record, principal)
            if record["status"] != "APPROVED":
                raise conflict("Pelaksanaan membutuhkan keputusan perubahan yang telah disetujui.")
            return await self.repository.write(
                session,
                "property",
                spec,
                principal,
                {
                    "status": "IMPLEMENTED",
                    "implementation_notes": reason,
                    "implemented_at": datetime.now(UTC),
                },
                identity,
                operation="implemented",
                audit_reason=reason,
            )

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
        if data.get("document_id"):
            await self.ports["work"].validate_document_reference(
                session, principal, data["document_id"]
            )
        if (
            name in {"change_orders", "payment_certificates"}
            and old
            and old["status"] == "SUBMITTED"
            and values.get("status") == "DRAFT"
        ):
            processes = await self.repository.table(session, "core", "business_processes")
            returned = await session.scalar(
                select(processes.c.process_id)
                .where(
                    *self.repository.scope(processes, principal),
                    processes.c.subject_id == data[spec.identifier],
                    processes.c.business_type
                    == ("CHANGE_ORDER" if name == "change_orders" else "PAYMENT_CERTIFICATE"),
                    processes.c.status == "RETURNED",
                )
                .with_for_update(read=True)
            )
            if returned is None:
                raise conflict("Perbaikan membutuhkan hasil pemeriksaan yang dikembalikan.")
        if (
            operation == "update"
            and old
            and name in {"change_orders", "payment_certificates"}
            and old["status"] in {"APPROVED", "IMPLEMENTED", "REJECTED"}
        ):
            raise conflict("Catatan keputusan tidak dapat diubah; buat pengajuan perubahan baru.")
        if name == "change_orders" and data.get("related_contract_id"):
            from alos.domains.legal.references import validate_change_contract

            await validate_change_contract(
                self.repository,
                session,
                principal,
                data.get("change_order_id"),
                data["related_contract_id"],
            )
        if name in {"change_orders", "payment_certificates"} and values.get("status") == "APPROVED":
            await require_reviews(
                self.repository,
                session,
                principal,
                "CHANGE_ORDER" if name == "change_orders" else "PAYMENT_CERTIFICATE",
                data[spec.identifier],
            )
            if name == "payment_certificates":
                from alos.notifications.business import enqueue_notice

                processes = await self.repository.table(session, "core", "business_processes")
                steps = await self.repository.table(session, "core", "business_process_steps")
                process = (
                    (
                        await session.execute(
                            select(processes).where(
                                *self.repository.scope(processes, principal),
                                processes.c.business_type == "PAYMENT_CERTIFICATE",
                                processes.c.subject_id == data[spec.identifier],
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if process:
                    finance_step = (
                        (
                            await session.execute(
                                select(steps).where(
                                    steps.c.process_id == process["process_id"],
                                    steps.c.revision == process["revision"],
                                    steps.c.code == "FINANCE_REVIEW",
                                )
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if finance_step:
                        await enqueue_notice(
                            self.repository,
                            session,
                            dict(process),
                            dict(finance_step),
                            "DECISION_COMPLETED",
                        )
        if (
            old
            and old.get("status")
            in {"CLOSED", "COMPLETED", "CANCELLED", "SUBMITTED", "APPROVED", "RESERVED", "SOLD"}
            and operation == "update"
        ):
            raise conflict("Historical or submitted Property records cannot be edited.")
        if data.get("project_id"):
            await self.ports["work"].validate_project_reference(
                session, principal, data["project_id"]
            )
        if name == "payment_certificates" and data.get("construction_update_id"):
            progress = await self.repository.row(
                session,
                "property",
                SPECS["construction_updates"],
                principal,
                data["construction_update_id"],
            )
            package = await self.repository.row(
                session,
                "property",
                SPECS["construction_packages"],
                principal,
                progress["construction_package_id"],
            )
            if (
                package["project_id"] != data["project_id"]
                or progress["progress_percent"] is None
                or not progress["summary"]
            ):
                raise conflict("Bukti kemajuan harus berasal dari proyek sertifikat dan lengkap.")
        if name == "construction_updates":
            package = await self.repository.row(
                session,
                "property",
                SPECS["construction_packages"],
                principal,
                data["construction_package_id"],
                lock=True,
            )
            await self.ports["work"].validate_project_reference(
                session, principal, package["project_id"]
            )
            if package["status"] != "IN_PROGRESS":
                raise conflict("Updates require a package in progress.")
            progress_percent = data.get("progress_percent")
            if progress_percent is not None and not Decimal(0) <= progress_percent <= Decimal(100):
                raise conflict("Progress must be between zero and one hundred.")
        if name == "quality_inspections" and old is None:
            values["inspector_actor_id"] = principal.actor_id
        if name == "quality_ncrs" and data.get("inspection_id"):
            inspection = await self.repository.row(
                session, "property", SPECS["quality_inspections"], principal, data["inspection_id"]
            )
            if inspection["project_id"] != data.get("project_id"):
                raise conflict("Inspection and project do not match.")
            if inspection["result"] == "PASS":
                raise conflict("A passed inspection cannot be the source of a nonconformance.")
        if name == "project_milestones":
            if values.get("status") == "COMPLETED" and not data.get("actual_date"):
                raise conflict("Completion requires an authoritative actual date.")
        if (
            name == "project_handovers"
            and values.get("status") == "COMPLETED"
            and not data.get("handover_date")
        ):
            raise conflict("Handover completion requires a date.")
        if (
            name == "payment_certificates"
            and values.get("status") == "SUBMITTED"
            and (data.get("amount") is None or not data.get("period"))
        ):
            raise conflict("Submission requires a recorded amount and period.")
        if (
            name == "change_orders"
            and values.get("status") == "SUBMITTED"
            and data.get("amount_delta") is None
        ):
            raise conflict("Submission requires a recorded amount delta.")

    async def approval_subject(
        self,
        session: AsyncSession,
        principal: Principal,
        subject_type: str,
        identity: str,
        requested_action: str | None,
        *,
        mode: str = "read",
    ) -> dict[str, Any]:
        authorize(principal, "property", "read" if mode == "read" else "write")
        if mode == "decide" and "DIVISION_LEAD" not in principal.roles:
            raise PlatformError(
                "BUSINESS_APPROVAL_DENIED",
                "Owner division lead authority is required.",
                status_code=403,
            )
        spec = next(
            (
                spec
                for spec in SPECS.values()
                if any(action.subject_type == subject_type for action in spec.material_actions)
            ),
            None,
        )
        if spec is None:
            raise conflict("Unsupported approval subject.")
        if mode in {"decide", "execute"} and spec.table in {
            "change_orders",
            "payment_certificates",
        }:
            await require_reviews(
                self.repository,
                session,
                principal,
                "CHANGE_ORDER" if spec.table == "change_orders" else "PAYMENT_CERTIFICATE",
                identity,
            )
        return await subject_snapshot(
            self.repository,
            session,
            "property",
            spec,
            principal,
            identity,
            requested_action,
            mode=mode,
            children={},
        )
