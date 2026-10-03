"""Owner record adapters produce bounded business packets, never workspace access."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.hr.records import SPECS as HR
from alos.domains.property.records import SPECS as PROPERTY
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.domains.sales.records import SPECS as SALES
from alos.identity import Principal

SUBJECTS = {
    "CAPABILITY_REQUEST": (
        "core",
        RecordSpec(
            "capability_business_requests",
            "request_id",
            "CapabilityBusinessRequest",
            None,
            {},
            frozenset(),
            frozenset(),
        ),
        ("SUBMITTED",),
    ),
    "CHANGE_ORDER": ("property", PROPERTY["change_orders"], ("SUBMITTED",)),
    "PAYMENT_CERTIFICATE": ("property", PROPERTY["payment_certificates"], ("SUBMITTED",)),
    "BOOKING": ("sales", SALES["bookings"], ("PENDING",)),
    "ONBOARDING": ("hr", HR["onboardings"], ("OPEN", "IN_PROGRESS")),
    "RECRUITMENT": ("hr", HR["recruitments"], ("OPEN",)),
    "OFFBOARDING": ("hr", HR["employees"], ("ACTIVE",)),
    "EMPLOYMENT_CONTRACT": ("hr", HR["employment_contracts"], ("IN_REVIEW",)),
}
PACKET_FIELDS = frozenset(
    {
        "project_id",
        "contract_number",
        "contract_type",
        "legal_review_required",
        "need",
        "goal",
        "business_context",
        "change_number",
        "description",
        "amount_delta",
        "schedule_impact_days",
        "related_contract_id",
        "contract_change_required",
        "document_id",
        "certificate_number",
        "construction_update_id",
        "period",
        "amount",
        "property_unit_id",
        "booking_date",
        "opportunity_id",
        "employee_id",
        "employee_number",
        "full_name",
        "email",
        "department_code",
        "position_title",
        "requesting_workspace_id",
        "employment_type",
        "headcount",
        "reason",
        "start_date",
        "end_date",
        "target_completion_date",
        "facility_request_id",
    }
)


def owner_domain(business_type: str) -> str:
    domain = SUBJECTS[business_type][0]
    return "work" if domain == "core" else domain


async def snapshot(
    repository: RecordRepository,
    session: AsyncSession,
    principal: Principal,
    business_type: str,
    subject_id: str,
    *,
    writing: bool = False,
) -> tuple[dict[str, Any], str]:
    if business_type not in SUBJECTS:
        raise conflict("Jenis alur proses tidak tersedia.")
    domain, spec, states = SUBJECTS[business_type]
    if writing:
        authorize(principal, "work" if domain == "core" else domain, "write")
    row = await repository.row(session, domain, spec, principal, subject_id, lock=True)
    repository.known_lifecycle(domain, spec, row)
    if row[spec.status_field] not in states:
        raise conflict("Status catatan bisnis tidak mendukung pengajuan ini.")
    packet = {key: value for key, value in row.items() if key in PACKET_FIELDS}
    packet["status"] = row[spec.status_field]
    if row.get("document_id"):
        documents = await repository.table(session, "core", "documents")
        document = (
            (
                await session.execute(
                    select(
                        documents.c.document_id,
                        documents.c.status,
                        documents.c.updated_at,
                    )
                    .where(
                        *repository.scope(documents, principal),
                        documents.c.document_id == row["document_id"],
                    )
                    .with_for_update(read=True)
                )
            )
            .mappings()
            .first()
        )
        if document is None:
            raise conflict("Dokumen pendukung tidak tersedia dalam ruang kerja pengajuan.")
        packet["document"] = dict(document)
        versions = await repository.table(session, "core", "document_versions")
        packet["document"]["current_version"] = await session.scalar(
            select(versions.c.version)
            .where(
                *repository.scope(versions, principal),
                versions.c.document_id == row["document_id"],
            )
            .order_by(versions.c.created_at.desc(), versions.c.record_id.desc())
            .limit(1)
        )
    if business_type in {"CHANGE_ORDER", "EMPLOYMENT_CONTRACT"}:
        links = await repository.table(session, "core", "business_record_links")
        contracts = await repository.table(session, "legal", "contracts")
        contract_refs = (
            (
                await session.execute(
                    select(
                        contracts.c.contract_id,
                        contracts.c.workspace_id,
                        contracts.c.status,
                        contracts.c.updated_at,
                    )
                    .join(
                        links,
                        (links.c.target_id == contracts.c.contract_id)
                        & (links.c.target_workspace_id == contracts.c.workspace_id),
                    )
                    .where(
                        links.c.tenant_id == principal.tenant_id,
                        links.c.organization_id == principal.organization_id,
                        contracts.c.tenant_id == principal.tenant_id,
                        contracts.c.organization_id == principal.organization_id,
                        links.c.source_workspace_id == principal.workspace_id,
                        links.c.source_id == subject_id,
                        links.c.source_type
                        == (
                            "PROPERTY_CHANGE_ORDER"
                            if business_type == "CHANGE_ORDER"
                            else "HR_EMPLOYMENT_CONTRACT"
                        ),
                        links.c.target_type == "LEGAL_CONTRACT",
                        links.c.relation == "GOVERNED_BY",
                    )
                    .order_by(contracts.c.contract_id)
                    .with_for_update(read=True, of=contracts)
                )
            )
            .mappings()
            .all()
        )
        packet["contracts"] = [dict(item) for item in contract_refs]
    if business_type in {"ONBOARDING", "EMPLOYMENT_CONTRACT"}:
        employee = await repository.row(
            session, "hr", HR["employees"], principal, row["employee_id"]
        )
        packet["employee"] = {key: value for key, value in employee.items() if key in PACKET_FIELDS}
    digest = hashlib.sha256(
        json.dumps({"record": row, "packet": packet}, sort_keys=True, default=str).encode()
    ).hexdigest()
    # JSON-safe exact decimals and dates; no document binary, payroll or secrets in packets.
    safe = json.loads(json.dumps(packet, default=str))
    return safe, digest
