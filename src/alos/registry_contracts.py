"""Typed authority projections exposed by the Backend registry boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alos.identity import Principal


class RegistryAuthorizationError(ValueError):
    """Raised when a registry definition cannot be safely authorized."""


@dataclass(frozen=True, slots=True)
class RegistryAuthorityView:
    subject_type: str
    subject_id: str
    version: str
    lifecycle: str
    owner: str
    risk: str
    tools: tuple[str, ...]
    permissions: tuple[str, ...]
    scope: tuple[str, ...]
    tenant_id: str
    organization_id: str
    workspace_id: str
    digest: str

    @classmethod
    def from_entry(cls, entry: Any) -> RegistryAuthorityView:
        payload = entry.payload
        owner = payload.get("owner") or payload.get("owner_actor_id")
        risk = payload.get("risk_level")
        tools = payload.get("tool_ids") or payload.get("backing_tool_ids") or []
        permissions = payload.get("permission_refs") or []
        scope = payload.get("scope_refs") or []
        if not isinstance(owner, str) or not owner.strip():
            raise RegistryAuthorizationError("registry definition has no authoritative owner")
        if not isinstance(risk, str) or not risk.strip():
            raise RegistryAuthorizationError("registry definition has no authoritative risk")
        references = (*tools, *permissions, *scope)
        if not all(isinstance(value, str) and value.strip() for value in references):
            raise RegistryAuthorizationError(
                "registry definition contains invalid authority references"
            )
        return cls(
            subject_type=entry.subject_type,
            subject_id=entry.subject_id,
            version=entry.version,
            lifecycle=entry.state.value,
            owner=owner,
            risk=risk,
            tools=tuple(sorted(set(tools))),
            permissions=tuple(sorted(set(permissions))),
            scope=tuple(sorted(set(scope))),
            tenant_id=entry.tenant_id,
            organization_id=entry.organization_id,
            workspace_id=entry.workspace_id,
            digest=entry.digest,
        )

    def authorize(self, principal: Principal) -> RegistryAuthorityView:
        if not principal.active:
            raise RegistryAuthorizationError("principal is inactive")
        if (
            principal.tenant_id != self.tenant_id
            or principal.organization_id != self.organization_id
            or principal.workspace_id != self.workspace_id
        ):
            raise RegistryAuthorizationError("principal is outside registry scope")
        if not self.scope:
            raise RegistryAuthorizationError("registry definition has no scope authority")
        if not set(self.permissions).issubset(principal.permissions):
            raise RegistryAuthorizationError("principal lacks registry permissions")
        if not set(self.scope).issubset(principal.scopes):
            raise RegistryAuthorizationError("principal lacks registry scope")
        return self
