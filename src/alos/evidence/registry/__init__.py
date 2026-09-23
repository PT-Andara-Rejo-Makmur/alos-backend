"""Future authoritative evidence reference registry."""

from alos.evidence.registry.service import (
    EvidenceConflictError,
    EvidenceRegistry,
    SqlEvidenceRegistry,
    resolve_registry_result,
)

__all__ = [
    "EvidenceConflictError",
    "EvidenceRegistry",
    "SqlEvidenceRegistry",
    "resolve_registry_result",
]
