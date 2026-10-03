"""Sales records bank and akad evidence without claiming bank or payment authority."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_references import DocumentReferencePort
from alos.domains.record_repository import RecordRepository, conflict
from alos.identity import Principal


async def validate_financing(
    repository: RecordRepository,
    work: DocumentReferencePort,
    session: AsyncSession,
    principal: Principal,
    data: dict[str, Any],
    values: dict[str, Any],
    old: dict[str, Any] | None,
) -> None:
    if old is None:
        values["responsible_actor_id"] = principal.actor_id
    for identity in data.get("document_ids", []):
        await work.validate_document_reference(session, principal, identity)
    status = values.get("status")
    if status in {"BANK_REVIEW", "SP3K_ISSUED"} and data["payment_method"] != "KPR":
        raise conflict("Pemeriksaan bank hanya berlaku untuk KPR.")
    if status == "READY" and data["payment_method"] == "KPR":
        raise conflict("KPR membutuhkan catatan SP3K sebelum akad.")
    if status in {"BANK_REVIEW", "SP3K_ISSUED"} and not data.get("bank_reference"):
        raise conflict("Bank dan nomor rujukan diperlukan.")
    if status == "SP3K_ISSUED" and (not data.get("sp3k_reference") or not data.get("sp3k_on")):
        raise conflict("Rujukan dan tanggal SP3K diperlukan.")
    if status == "AKAD_COMPLETED" and not data.get("akad_on"):
        raise conflict("Tanggal pelaksanaan akad diperlukan.")
    if data.get("sp3k_on") and data.get("akad_on") and data["akad_on"] < data["sp3k_on"]:
        raise conflict("Tanggal akad mendahului SP3K.")
    for field in ("sp3k_on", "akad_on"):
        if data.get(field) and data[field] > datetime.now(UTC).date():
            raise conflict("Bukti pelaksanaan tidak dapat dicatat pada tanggal mendatang.")
