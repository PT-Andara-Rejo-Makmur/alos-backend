"""Authoritative registry for canonical AgentDefinition payloads."""

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import VersionedContractRegistry

AGENT_DEFINITION_SCHEMA_ID = "https://schemas.alos.dev/v1/agent/agent-definition.schema.json"


class AgentRegistry(VersionedContractRegistry):
    def __init__(self, contracts: CanonicalContractCatalog, audit: AuditSink) -> None:
        super().__init__(
            subject_type="agent",
            schema_id=AGENT_DEFINITION_SCHEMA_ID,
            id_field="agent_id",
            version_field="agent_version",
            contracts=contracts,
            audit=audit,
        )
