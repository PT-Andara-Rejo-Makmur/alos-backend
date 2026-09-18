"""Authoritative registry for canonical CapabilityDefinition payloads."""

from typing import Any

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
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

    def catalog_snapshot(self, *, principal: Principal) -> tuple[dict[str, Any], ...]:
        """Project authorized ACTIVE definitions into canonical read-only catalog items."""

        items: list[dict[str, Any]] = []
        for entry in self.list_authorized_entries(principal=principal):
            payload = entry.payload
            item: dict[str, Any] = {
                "capability_id": entry.subject_id,
                "version": entry.version,
                "name": payload["name"],
                "purpose": payload["purpose"],
                "capability_type": payload["capability_type"],
                "risk_level": payload.get("risk_level", "MEDIUM"),
                "availability": payload.get("availability", "AVAILABLE"),
                "configuration_status": payload.get(
                    "configuration_status", "CONFIGURED"
                ),
                "backing_tool_ids": payload.get("backing_tool_ids", []),
                "permission_refs": payload.get("permission_refs", []),
                "scope_refs": payload.get("scope_refs", []),
            }
            metadata = payload.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("keywords"), list):
                item["keywords"] = metadata["keywords"]
            items.append(item)
        return tuple(items)
