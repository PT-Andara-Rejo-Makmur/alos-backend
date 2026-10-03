"""Company rules resolve divisions and permissions without choosing human actors."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from alos.security.errors import PlatformError


@dataclass(frozen=True)
class StepRequirement:
    code: str
    kind: str
    domain: str
    role: str
    permission: str
    instruction: str
    reason: str
    independent: bool = False


def review(code: str, domain: str, instruction: str, reason: str) -> StepRequirement:
    return StepRequirement(
        code,
        "REVIEW",
        domain,
        "DIVISION_LEAD",
        "work.write" if domain == "owner" else f"{domain}.write",
        instruction,
        reason,
    )


def requirements(
    business_type: str, facts: dict[str, Any], rules: dict[str, Any]
) -> tuple[StepRequirement, ...]:
    """Facts originate in owner records. Missing money limits never invent company policy."""
    steps: list[StepRequirement]
    if business_type == "CAPABILITY_REQUEST":
        steps = [
            review(
                "BUSINESS_VALIDATION",
                "owner",
                "Periksa kebutuhan dan tujuan bisnis",
                "Permintaan bantuan ALOS",
            )
        ]
    elif business_type == "EMPLOYMENT_CONTRACT":
        steps = [
            review("HR_CONTRACT_REVIEW", "hr", "Periksa syarat hubungan kerja", "Kontrak karyawan")
        ]
        if facts.get("legal_review_required") or rules.get("legal_review_required"):
            steps.append(
                review(
                    "LEGAL_REVIEW",
                    "legal",
                    "Periksa ketentuan kontrak kerja",
                    "Kebutuhan pemeriksaan hukum",
                )
            )
    elif business_type == "CHANGE_ORDER":
        steps = [
            review(
                "TECHNICAL_REVIEW", "property", "Periksa perubahan teknis", "Perubahan pekerjaan"
            )
        ]
        if Decimal(str(facts.get("amount_delta") or "0")) != 0:
            steps.append(
                review("FINANCE_REVIEW", "finance", "Periksa dampak biaya", "Perubahan biaya")
            )
        if facts.get("related_contract_id") or facts.get("contract_change_required"):
            steps.append(
                review("LEGAL_REVIEW", "legal", "Periksa perubahan kontrak", "Kontrak terkait")
            )
    elif business_type == "PAYMENT_CERTIFICATE":
        steps = [
            review(
                "PROPERTY_VERIFICATION",
                "property",
                "Verifikasi pekerjaan dan bukti",
                "Sertifikat pekerjaan",
            ),
            review(
                "FINANCE_REVIEW", "finance", "Periksa anggaran dan dokumen", "Kewajiban pembayaran"
            ),
        ]
    elif business_type == "BOOKING":
        steps = [review("UNIT_REVIEW", "property", "Pastikan unit tersedia", "Pemesanan unit")]
    elif business_type == "ONBOARDING":
        steps = [review("HR_READINESS", "hr", "Periksa data karyawan", "Kesiapan karyawan")]
        steps.extend(
            [
                StepRequirement(
                    "IT_ACCESS",
                    "EXECUTION",
                    "it",
                    "IT_ADMIN",
                    "identity.accounts.manage",
                    "Siapkan akun dan akses",
                    "Akses kerja",
                ),
                StepRequirement(
                    "GA_READINESS",
                    "EXECUTION",
                    "hr",
                    "DIVISION_LEAD",
                    "hr.write",
                    "Siapkan fasilitas kerja",
                    "Kesiapan fasilitas",
                ),
                review(
                    "DIVISION_READINESS", "owner", "Pastikan kesiapan divisi", "Kesiapan penugasan"
                ),
            ]
        )
    elif business_type == "OFFBOARDING":
        steps = [
            review(
                "WORK_HANDOVER",
                "owner",
                "Periksa serah terima pekerjaan",
                "Pengakhiran hubungan kerja",
            ),
            review("ASSET_RETURN", "hr", "Periksa pengembalian aset", "Pengembalian fasilitas"),
        ]
        for enabled, domain, code, instruction in (
            (
                "finance_settlement_required",
                "finance",
                "FINANCE_SETTLEMENT",
                "Periksa penyelesaian keuangan",
            ),
            ("legal_review_required", "legal", "LEGAL_REVIEW", "Periksa kewajiban hukum"),
        ):
            if rules.get(enabled):
                steps.append(review(code, domain, instruction, "Aturan pengakhiran hubungan kerja"))
        steps.append(
            StepRequirement(
                "IT_REVOCATION",
                "EXECUTION",
                "it",
                "IT_ADMIN",
                "identity.memberships.manage",
                "Cabut akses dan sesi karyawan",
                "Pengamanan akses",
            )
        )
    else:
        raise PlatformError(
            "PROCESS_TYPE_INVALID", "Jenis alur proses tidak tersedia.", status_code=422
        )

    limit = rules.get("executive_amount_limit")
    amount = facts.get("amount_delta", facts.get("amount"))
    executive = bool(rules.get("executive_required")) or (
        limit is not None and amount is not None and abs(Decimal(str(amount))) > Decimal(str(limit))
    )
    if executive:
        steps.append(
            StepRequirement(
                "EXECUTIVE_DECISION",
                "DECISION",
                "executive",
                "EXECUTIVE",
                "approval.approve",
                "Putuskan pengajuan sesuai kewenangan",
                "Melewati kewenangan sesuai aturan perusahaan",
                True,
            )
        )
    if rules.get("executive_acknowledgement"):
        steps.append(
            StepRequirement(
                "EXECUTIVE_ACKNOWLEDGEMENT",
                "ACKNOWLEDGEMENT",
                "executive",
                "EXECUTIVE",
                "work.read",
                "Baca informasi sesuai aturan perusahaan",
                "Untuk diketahui",
                False,
            )
        )
    independent = set(rules.get("independent_steps", []))
    return tuple(
        StepRequirement(
            step.code,
            step.kind,
            step.domain,
            step.role,
            step.permission,
            step.instruction,
            step.reason,
            step.independent or step.code in independent,
        )
        for step in steps
    )
