"""Authoritative registry for canonical CapabilityDefinition payloads."""

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import VersionedContractRegistry

CAPABILITY_DEFINITION_SCHEMA_ID = (
    "https://schemas.alos.dev/v1/capability/capability-definition.schema.json"
)


class CapabilityRegistry(VersionedContractRegistry):
    def __init__(self, contracts: CanonicalContractCatalog, audit: AuditSink) -> None:
        super().__init__(
            subject_type="capability",
            schema_id=CAPABILITY_DEFINITION_SCHEMA_ID,
            id_field="capability_id",
            version_field="version",
            contracts=contracts,
            audit=audit,
        )
