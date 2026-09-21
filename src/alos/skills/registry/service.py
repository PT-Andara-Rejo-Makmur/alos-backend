"""Authoritative registry for canonical SkillDefinition payloads."""

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import RegistryStore, VersionedContractRegistry

SKILL_DEFINITION_SCHEMA_ID = "https://schemas.alos.dev/v1/skill/skill-definition.schema.json"


class SkillRegistry(VersionedContractRegistry):
    def __init__(
        self,
        contracts: CanonicalContractCatalog,
        audit: AuditSink | None = None,
        store: RegistryStore | None = None,
    ) -> None:
        super().__init__(
            subject_type="skill",
            schema_id=SKILL_DEFINITION_SCHEMA_ID,
            id_field="skill_id",
            version_field="skill_version",
            contracts=contracts,
            audit=audit,
            store=store,
        )
