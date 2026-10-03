"""Create one Finance obligation from an approved, explicitly shared business packet."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import insert, select

from alos.audit import AuditEvent
from alos.domains.finance.records import SPECS
from alos.domains.finance.service import FinanceService
from alos.domains.record_repository import authorize, conflict
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import assigned, revalidate
from alos.processes.service import ProcessService


async def payable_from_certificate(
    finance: FinanceService,
    processes: ProcessService,
    principal: Principal,
    process_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    authorize(principal, "finance", "write")
    repository = finance.repository
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        process = await processes.load(session, principal, process_id)
        if process["business_type"] != "PAYMENT_CERTIFICATE" or process["status"] != "COMPLETED":
            raise conflict("Pemeriksaan Payment Certificate belum selesai.")
        steps = await processes.steps(session, process_id)
        if not any(
            step["code"] == "FINANCE_REVIEW" and assigned(step, principal) for step in steps
        ):
            raise conflict("Pengajuan ini belum ditujukan ke kewenangan Finance aktif.")
        policy = await repository.table(session, "core", "business_process_policies")
        version = await session.scalar(
            select(policy.c.version)
            .where(
                policy.c.policy_id == process["policy_id"],
                policy.c.tenant_id == principal.tenant_id,
                policy.c.organization_id == principal.organization_id,
                policy.c.active.is_(True),
            )
            .with_for_update(read=True)
        )
        if version != process["policy_version"]:
            raise conflict("Aturan pengajuan berubah; pemeriksaan ulang diperlukan.")
        certificates = await repository.table(session, "property", "payment_certificates")
        certificate = (
            (
                await session.execute(
                    select(certificates)
                    .where(
                        certificates.c.tenant_id == principal.tenant_id,
                        certificates.c.organization_id == principal.organization_id,
                        certificates.c.workspace_id == process["workspace_id"],
                        certificates.c.payment_certificate_id == process["subject_id"],
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if (
            certificate["status"] != "APPROVED"
            or str(certificate["amount"]) != process["packet"]["amount"]
        ):
            raise conflict("Sertifikat belum disetujui atau nilai kewajiban berubah.")
        links = await repository.table(session, "core", "business_record_links")
        origin = (
            (
                await session.execute(
                    select(links).where(
                        links.c.tenant_id == principal.tenant_id,
                        links.c.organization_id == principal.organization_id,
                        links.c.source_type == "PROPERTY_PAYMENT_CERTIFICATE",
                        links.c.source_id == certificate["payment_certificate_id"],
                        links.c.target_type == "FINANCE_PAYABLE",
                        links.c.relation == "CREATED_FROM",
                    )
                )
            )
            .mappings()
            .first()
        )
        if origin:
            if origin["target_workspace_id"] != principal.workspace_id:
                raise conflict("Kewajiban telah dibuat pada context Finance lain.")
            row = await repository.row(
                session, "finance", SPECS["payables"], principal, origin["target_id"]
            )
            return repository.project(SPECS["payables"], row, principal)
        table = await repository.table(session, "finance", "payables")
        values = repository.values(
            table,
            {
                **payload,
                "reference": "Payment Certificate " + certificate["certificate_number"],
                "description": "Kewajiban atas pekerjaan terverifikasi",
                "amount": str(certificate["amount"]),
            },
        )
        await finance._prepare(session, SPECS["payables"], principal, values, None, "create")
        await finance._rule(session, SPECS["payables"], principal, values, None, "create")
        payable = await repository.write(
            session,
            "finance",
            SPECS["payables"],
            principal,
            values,
            None,
            operation="certificate_obligation",
        )
        await session.execute(
            insert(links).values(
                link_id=uuid4().hex,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                source_workspace_id=process["workspace_id"],
                source_type="PROPERTY_PAYMENT_CERTIFICATE",
                source_id=certificate["payment_certificate_id"],
                target_workspace_id=principal.workspace_id,
                target_type="FINANCE_PAYABLE",
                target_id=payable["payable_id"],
                relation="CREATED_FROM",
                process_id=process_id,
                created_by=principal.actor_id,
                correlation_id=current_correlation_id(),
                created_at=datetime.now(UTC),
            )
        )
        return payable


async def link_payment_transaction(
    finance: FinanceService, principal: Principal, payment_id: str, transaction_id: str, reason: str
) -> dict[str, Any]:
    authorize(principal, "finance", "write")
    repository = finance.repository
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        payment = await repository.row(
            session, "finance", SPECS["payable_payments"], principal, payment_id, lock=True
        )
        transaction = await repository.row(
            session, "finance", SPECS["bank_transactions"], principal, transaction_id, lock=True
        )
        if (
            transaction["direction"] != "OUT"
            or transaction["amount"] != payment["amount"]
            or transaction["transaction_date"] != payment["payment_date"]
        ):
            raise conflict("Transaksi bank harus sesuai tanggal, arah dan nilai pembayaran.")
        links = await repository.table(session, "core", "business_record_links")
        old = (
            (
                await session.execute(
                    select(links).where(
                        links.c.tenant_id == principal.tenant_id,
                        links.c.organization_id == principal.organization_id,
                        links.c.source_type == "FINANCE_PAYABLE_PAYMENT",
                        links.c.source_id == payment_id,
                        links.c.target_type == "FINANCE_BANK_TRANSACTION",
                        links.c.relation == "SETTLED_BY",
                    )
                )
            )
            .mappings()
            .first()
        )
        if old:
            if old["target_id"] != transaction_id:
                raise conflict("Pembayaran telah dihubungkan ke transaksi bank lain.")
            return dict(jsonable_encoder(dict(old)))
        reused = await session.scalar(
            select(links.c.link_id).where(
                links.c.tenant_id == principal.tenant_id,
                links.c.organization_id == principal.organization_id,
                links.c.target_type == "FINANCE_BANK_TRANSACTION",
                links.c.target_id == transaction_id,
                links.c.relation == "SETTLED_BY",
            )
        )
        if reused:
            raise conflict("Transaksi bank telah digunakan sebagai bukti pembayaran lain.")
        stamp = datetime.now(UTC)
        record: dict[str, Any] = {
            "link_id": uuid4().hex,
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "source_workspace_id": principal.workspace_id,
            "source_type": "FINANCE_PAYABLE_PAYMENT",
            "source_id": payment_id,
            "target_workspace_id": principal.workspace_id,
            "target_type": "FINANCE_BANK_TRANSACTION",
            "target_id": transaction_id,
            "relation": "SETTLED_BY",
            "process_id": None,
            "created_by": principal.actor_id,
            "correlation_id": current_correlation_id(),
            "created_at": stamp,
        }
        await session.execute(insert(links).values(**record))
        await repository.audit.append_in_session(
            session,
            AuditEvent(
                event_type="finance.payment_bank_linked",
                entity_type="business_record_link",
                entity_id=record["link_id"],
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=current_correlation_id(),
                outcome="SUCCEEDED",
                occurred_at=stamp,
                reason=reason,
                metadata={"payment_id": payment_id, "transaction_id": transaction_id},
            ),
        )
        return dict(jsonable_encoder(record))
