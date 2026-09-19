"""Authoritative registry for canonical CapabilityDefinition payloads."""

from typing import Any

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.identity import Principal
from alos.registry import RegistryState, VersionedContractRegistry
from alos.registry_contracts import RegistryAuthorityView, RegistryAuthorizationError

CAPABILITY_DEFINITION_SCHEMA_ID = (
    "https://schemas.alos.dev/v1/capability/capability-definition.schema.json"
)
CAPABILITY_DETAIL_SCHEMA_ID = (
    "https://schemas.alos.dev/v1/capability/capability-detail.schema.json"
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

    def detail(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        capability_id: str,
        principal: Principal,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Project an authorized registered version into the canonical CapabilityDetail."""

        if principal is None:
            raise RegistryAuthorizationError("principal is required")
        matching = [
            entry
            for (entry_tenant, entry_workspace, subject_id, _), entry in self._entries.items()
            if entry_tenant == tenant_id
            and entry_workspace == workspace_id
            and subject_id == capability_id
            and entry.organization_id == principal.organization_id
            and (version is None or entry.version == version)
        ]
        if not matching:
            raise LookupError("capability version was not found in tenant workspace")
        entry = (
            max(matching, key=lambda item: _version_key(item.version))
            if version is None
            else matching[0]
        )
        if entry.state is RegistryState.DRAFT:
            # DRAFT carries no production authority and is visible only to its creator.
            if entry.created_by != principal.actor_id:
                raise LookupError("capability version was not found in tenant workspace")
            RegistryAuthorityView.from_entry(entry).authorize(principal)
        elif entry.state is RegistryState.ACTIVE:
            RegistryAuthorityView.from_entry(entry).authorize(principal)
        else:
            # APPROVED, SUSPENDED, and RETIRED states fail closed for consumers.
            raise LookupError("capability version was not found in tenant workspace")
        try:
            return self._contracts.validate(
                CAPABILITY_DETAIL_SCHEMA_ID, self._detail_projection(entry)
            )
        except ContractValidationError as exc:  # pragma: no cover - deterministic projection
            raise ValueError("capability detail projection failed contract validation") from exc

    @staticmethod
    def _detail_projection(entry: Any) -> dict[str, Any]:
        payload = entry.payload
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        is_draft = entry.state is RegistryState.DRAFT
        tools = payload.get("backing_tool_ids") or payload.get("tool_ids") or []
        prohibited = payload.get("prohibited_actions") or metadata.get("prohibited_actions") or []
        evidence = (
            payload.get("evidence_requirements") or metadata.get("evidence_requirements") or []
        )
        tests = payload.get("test_requirements") or metadata.get("test_requirements") or []
        dependencies = payload.get("dependency_refs") or metadata.get("dependency_refs") or []
        constraints = payload.get("constraints") or []
        detail: dict[str, Any] = {
            "capability_id": entry.subject_id,
            "version": entry.version,
            "name": payload["name"],
            "purpose": payload["purpose"],
            "owner": payload["owner"],
            "capability_type": payload["capability_type"],
            "lifecycle_state": entry.state.value,
            "risk_level": payload.get("risk_level", "MEDIUM"),
            "availability": payload.get("availability")
            or ("UNAVAILABLE" if is_draft else "AVAILABLE"),
            "configuration_status": payload.get("configuration_status")
            or "NEEDS_CONFIGURATION",
            "scope_refs": list(payload.get("scope_refs") or []),
            "permission_refs": list(payload.get("permission_refs") or []),
            "backing_tool_ids": list(tools),
            "prohibited_actions": list(prohibited),
            "evidence_requirements": list(evidence),
            "test_requirements": list(tests),
            # Backend governance owns the human gate; AI values never decide it.
            "human_gate_required": bool(metadata.get("human_gate_required", True)),
            "created_by": entry.created_by,
            "correlation_id": entry.correlation_id,
            "created_at": entry.created_at.isoformat(),
        }
        if dependencies:
            detail["dependency_refs"] = list(dependencies)
        if constraints:
            detail["constraints"] = list(constraints)
        if entry.decision_id:
            detail["decision_id"] = entry.decision_id
        if entry.release_id:
            detail["release_id"] = entry.release_id
        return detail


def _version_key(version: str) -> tuple[int, ...]:
    core = version.split("-", 1)[0].split("+", 1)[0]
    parts: list[int] = []
    for piece in core.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)
