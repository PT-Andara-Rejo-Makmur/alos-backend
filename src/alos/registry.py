"""Shared mechanics for authoritative, versioned definition registries."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from alos.audit import AuditEvent, AuditSink
from alos.contracts import CanonicalContractCatalog
from alos.identity import Principal
from alos.registry_contracts import RegistryAuthorityView, RegistryAuthorizationError


class RegistryConflictError(ValueError):
    pass


class RegistryNotFoundError(LookupError):
    pass


class RegistryState(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class NullAuditSink:
    async def append(self, event: Any) -> None:
        return None


class DecisionAuthority(StrEnum):
    IT = "IT"
    DIRECTOR = "DIRECTOR"


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    subject_type: str
    subject_id: str
    version: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    payload: dict[str, Any]
    digest: str
    state: RegistryState
    created_by: str
    correlation_id: str
    created_at: datetime
    decision_id: str | None = None
    release_id: str | None = None


class RegistryStore(Protocol):
    async def save(self, entry: RegistryEntry) -> None: ...
    async def load_subject_type(self, subject_type: str) -> tuple[RegistryEntry, ...]: ...


class InMemoryRegistryStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str, str], RegistryEntry] = {}

    async def save(self, entry: RegistryEntry) -> None:
        key = (
            entry.subject_type,
            entry.tenant_id,
            entry.workspace_id,
            entry.subject_id,
            entry.version,
        )
        self._records[key] = VersionedContractRegistry._copy_entry(entry)

    async def load_subject_type(self, subject_type: str) -> tuple[RegistryEntry, ...]:
        return tuple(
            VersionedContractRegistry._copy_entry(item)
            for item in self._records.values()
            if item.subject_type == subject_type
        )


class VersionedContractRegistry:
    """Validate canonical definitions, retain immutable snapshots, and gate activation."""

    def __init__(
        self,
        *,
        subject_type: str,
        schema_id: str,
        id_field: str,
        version_field: str,
        contracts: CanonicalContractCatalog,
        audit: AuditSink | None = None,
        store: RegistryStore | None = None,
    ) -> None:
        self._subject_type = subject_type
        self._schema_id = schema_id
        self._id_field = id_field
        self._version_field = version_field
        self._contracts = contracts
        self._audit = audit or NullAuditSink()
        self._store = store or InMemoryRegistryStore()
        self._entries: dict[tuple[str, str, str, str], RegistryEntry] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        actor_id: str,
        correlation_id: str,
    ) -> RegistryEntry:
        validated = self._contracts.validate(self._schema_id, payload)
        self._require_matching_context(validated, tenant_id, organization_id, workspace_id)
        subject_id = str(validated[self._id_field])
        version = str(validated[self._version_field])
        digest = self._digest(validated)
        key = (tenant_id, workspace_id, subject_id, version)
        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                if existing.digest == digest:
                    return self._copy_entry(existing)
                raise RegistryConflictError("immutable registry version already exists")
            entry = RegistryEntry(
                subject_type=self._subject_type,
                subject_id=subject_id,
                version=version,
                tenant_id=tenant_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                payload=copy.deepcopy(validated),
                digest=digest,
                state=RegistryState.DRAFT,
                created_by=actor_id,
                correlation_id=correlation_id,
                created_at=datetime.now(UTC),
            )
            self._entries[key] = entry
            await self._store.save(entry)
        await self._record(entry, actor_id, "registry.version.created", "DRAFT")
        return self._copy_entry(entry)

    async def approve(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
        actor_id: str,
        decision_id: str,
        authority: DecisionAuthority | str,
        correlation_id: str,
    ) -> RegistryEntry:
        try:
            resolved_authority = (
                DecisionAuthority(authority) if isinstance(authority, str) else authority
            )
        except ValueError as exc:
            raise RegistryConflictError(
                "AI recommendation cannot approve a registry version"
            ) from exc
        if resolved_authority not in {DecisionAuthority.IT, DecisionAuthority.DIRECTOR}:
            raise RegistryConflictError("AI recommendation cannot approve a registry version")
        entry = await self._transition(
            tenant_id,
            workspace_id,
            subject_id,
            version,
            expected={RegistryState.DRAFT},
            target=RegistryState.APPROVED,
            decision_id=decision_id,
            correlation_id=correlation_id,
        )
        await self._record(entry, actor_id, "registry.version.approved", resolved_authority.value)
        return self._copy_entry(entry)

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
        entry = await self._transition(
            tenant_id,
            workspace_id,
            subject_id,
            version,
            expected={RegistryState.APPROVED},
            target=RegistryState.ACTIVE,
            release_id=release_id,
            correlation_id=correlation_id,
        )
        await self._record(entry, actor_id, "registry.version.activated", "ACTIVE")
        return self._copy_entry(entry)

    async def suspend(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
        actor_id: str,
        correlation_id: str,
    ) -> RegistryEntry:
        entry = await self._transition(
            tenant_id,
            workspace_id,
            subject_id,
            version,
            expected={RegistryState.ACTIVE},
            target=RegistryState.SUSPENDED,
            correlation_id=correlation_id,
        )
        await self._record(entry, actor_id, "registry.version.suspended", "SUSPENDED")
        return self._copy_entry(entry)

    def get(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
    ) -> RegistryEntry:
        key = (tenant_id, workspace_id, subject_id, version)
        entry = self._entries.get(key)
        if entry is None:
            raise RegistryNotFoundError("registry version was not found in tenant workspace")
        return self._copy_entry(entry)

    def get_authorized(
        self,
        *,
        principal: Principal | None,
        subject_id: str,
        version: str,
    ) -> RegistryAuthorityView:
        """Return only an ACTIVE definition authorized by the Backend principal."""
        if principal is None:
            raise RegistryAuthorizationError("principal is required")
        entry = self.get(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            subject_id=subject_id,
            version=version,
        )
        if entry.organization_id != principal.organization_id:
            raise RegistryAuthorizationError("registry organization is outside principal scope")
        if entry.state is not RegistryState.ACTIVE:
            raise RegistryAuthorizationError("registry lifecycle is not ACTIVE")
        return RegistryAuthorityView.from_entry(entry).authorize(principal)

    def list_authorized_entries(self, *, principal: Principal | None) -> tuple[RegistryEntry, ...]:
        """Return immutable ACTIVE entries usable by a Backend-authenticated principal."""

        if principal is None:
            raise RegistryAuthorizationError("principal is required")
        authorized: list[RegistryEntry] = []
        for entry in self._entries.values():
            if (
                entry.tenant_id != principal.tenant_id
                or entry.organization_id != principal.organization_id
                or entry.workspace_id != principal.workspace_id
                or entry.state is not RegistryState.ACTIVE
            ):
                continue
            try:
                RegistryAuthorityView.from_entry(entry).authorize(principal)
            except RegistryAuthorizationError:
                continue
            authorized.append(self._copy_entry(entry))
        return tuple(
            sorted(
                authorized,
                key=lambda item: (item.subject_id, self._semver(item.version)),
            )
        )

    def list_entries(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        subject_id: str | None = None,
    ) -> tuple[RegistryEntry, ...]:
        entries = [
            self._copy_entry(entry)
            for entry in self._entries.values()
            if entry.tenant_id == tenant_id
            and entry.organization_id == organization_id
            and entry.workspace_id == workspace_id
            and (subject_id is None or entry.subject_id == subject_id)
        ]
        return tuple(
            sorted(entries, key=lambda item: (item.subject_id, self._semver(item.version)))
        )

    def versions(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        subject_id: str,
    ) -> tuple[str, ...]:
        return tuple(
            entry.version
            for entry in self.list_entries(
                tenant_id=tenant_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                subject_id=subject_id,
            )
        )

    def latest_entry(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        subject_id: str,
    ) -> RegistryEntry:
        entries = self.list_entries(
            tenant_id=tenant_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
        )
        if not entries:
            raise RegistryNotFoundError("registry subject was not found")
        return entries[-1]

    async def hydrate(self) -> None:
        loaded = await self._store.load_subject_type(self._subject_type)
        async with self._lock:
            self._entries = {
                (item.tenant_id, item.workspace_id, item.subject_id, item.version): item
                for item in loaded
            }

    async def _transition(
        self,
        tenant_id: str,
        workspace_id: str,
        subject_id: str,
        version: str,
        *,
        expected: set[RegistryState],
        target: RegistryState,
        correlation_id: str,
        decision_id: str | None = None,
        release_id: str | None = None,
    ) -> RegistryEntry:
        key = (tenant_id, workspace_id, subject_id, version)
        async with self._lock:
            current = self._entries.get(key)
            if current is None:
                raise RegistryNotFoundError("registry version was not found in tenant workspace")
            if current.state not in expected:
                raise RegistryConflictError(
                    f"cannot transition {current.state.value} to {target.value}"
                )
            updated = replace(
                current,
                state=target,
                correlation_id=correlation_id,
                decision_id=decision_id or current.decision_id,
                release_id=release_id or current.release_id,
            )
            self._entries[key] = updated
            await self._store.save(updated)
            return updated

    async def _record(
        self,
        entry: RegistryEntry,
        actor_id: str,
        event_type: str,
        outcome: str,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.append(
            AuditEvent(
                event_type=event_type,
                entity_type=entry.subject_type,
                entity_id=entry.subject_id,
                tenant_id=entry.tenant_id,
                organization_id=entry.organization_id,
                workspace_id=entry.workspace_id,
                actor_id=actor_id,
                correlation_id=entry.correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                metadata={"version": entry.version, "digest": entry.digest},
            )
        )

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _copy_entry(entry: RegistryEntry) -> RegistryEntry:
        return replace(entry, payload=copy.deepcopy(entry.payload))

    @staticmethod
    def _require_matching_context(
        payload: dict[str, Any],
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
    ) -> None:
        expected = {
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
        }
        for field_name, value in expected.items():
            supplied = payload.get(field_name)
            if supplied is not None and str(supplied) != value:
                raise RegistryConflictError(f"{field_name} does not match authority context")

    @staticmethod
    def _semver(version: str) -> tuple[int, int, int, str]:
        core, _, suffix = version.partition("-")
        parts = core.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise RegistryConflictError(f"invalid semantic version: {version}")
        return int(parts[0]), int(parts[1]), int(parts[2]), suffix
