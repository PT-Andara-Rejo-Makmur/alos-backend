"""Authoritative registry for canonical SkillDefinition payloads."""

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import RegistryState, VersionedContractRegistry

SKILL_DEFINITION_SCHEMA_ID = "https://schemas.alos.dev/v1/skill/skill-definition.schema.json"


class SkillRegistry(VersionedContractRegistry):
    def __init__(self, contracts: CanonicalContractCatalog, audit: AuditSink | None = None) -> None:
        super().__init__(
            subject_type="skill",
            schema_id=SKILL_DEFINITION_SCHEMA_ID,
            id_field="skill_id",
            version_field="skill_version",
            contracts=contracts,
            audit=audit,
        )

    def list_active(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
    ) -> list[dict[str, object]]:
        return [
            dict(entry.payload)
            for entry in self._entries.values()
            if (
                entry.tenant_id == tenant_id
                and entry.organization_id == organization_id
                and entry.workspace_id == workspace_id
                and entry.state is RegistryState.ACTIVE
            )
        ]
