"""Authoritative registry for canonical AgentDefinition payloads."""

from __future__ import annotations

from alos.audit import AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.registry import (
    RegistryConflictError,
    RegistryEntry,
    RegistryStore,
    VersionedContractRegistry,
)

AGENT_DEFINITION_SCHEMA_ID = "https://schemas.alos.dev/v1/agent/agent-definition.schema.json"


class AgentRegistry(VersionedContractRegistry):
    def __init__(
        self,
        contracts: CanonicalContractCatalog,
        audit: AuditSink,
        store: RegistryStore | None = None,
        *,
        release_governed: bool = False,
    ) -> None:
        self._release_governed = release_governed
        super().__init__(
            subject_type="agent",
            schema_id=AGENT_DEFINITION_SCHEMA_ID,
            id_field="agent_id",
            version_field="agent_version",
            contracts=contracts,
            audit=audit,
            store=store,
        )

    async def activate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
        actor_id: str,
        release_id: str,
        correlation_id: str,
    ) -> RegistryEntry:
        """Reject direct activation when production release governance owns lifecycle."""

        if self._release_governed:
            raise RegistryConflictError(
                "agent registry activation requires governed release orchestration"
            )
        return await super().activate(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            version=version,
            actor_id=actor_id,
            release_id=release_id,
            correlation_id=correlation_id,
        )

    async def activate_test_only(
        self,
        *,
        test_mode_authorized: bool,
        tenant_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
        actor_id: str,
        release_id: str,
        correlation_id: str,
    ) -> RegistryEntry:
        """Retain the deterministic bootstrap bypass only behind an explicit environment gate."""

        if not test_mode_authorized:
            raise RegistryConflictError("test-only registry activation is disabled")
        return await super().activate(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            version=version,
            actor_id=actor_id,
            release_id=release_id,
            correlation_id=correlation_id,
        )

    async def publish_committed(self, entry: RegistryEntry) -> None:
        """Publish a database-committed lifecycle snapshot to the local read cache."""

        if entry.subject_type != "agent":
            raise RegistryConflictError("only agent definitions may be published here")
        key = (entry.tenant_id, entry.workspace_id, entry.subject_id, entry.version)
        async with self._lock:
            current = self._entries.get(key)
            if current is not None and (
                current.digest != entry.digest
                or current.organization_id != entry.organization_id
            ):
                raise RegistryConflictError("committed registry identity does not match cache")
            self._entries[key] = self._copy_entry(entry)
