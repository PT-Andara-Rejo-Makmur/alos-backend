"""Typed server-side context bundle used by backend authorization and projection flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alos.identity import Principal


@dataclass(frozen=True, slots=True)
class ContextBuildRequest:
    goal: str = ""
    capability_ids: tuple[str, ...] = ()
    tool_ids: tuple[str, ...] = ()
    budget_hint: int | None = None
    token_hint: int | None = None
    division_id: str | None = None
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextBundle:
    status: str
    context_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    actor_id: str
    correlation_id: str
    division_id: str | None = None
    project_id: str | None = None
    scope_refs: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    budget: int = 0
    token_limit: int = 0
    denial_reason: str | None = None
    needs_info_reason: str | None = None
    data_classification: str = "INTERNAL"
    evidence_refs: tuple[str, ...] = ()
    items: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "context_id": self.context_id,
            "tenant_id": self.tenant_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "correlation_id": self.correlation_id,
            "division_id": self.division_id,
            "project_id": self.project_id,
            "scope_refs": list(self.scope_refs),
            "allowed_capabilities": list(self.allowed_capabilities),
            "allowed_tools": list(self.allowed_tools),
            "budget": self.budget,
            "token_limit": self.token_limit,
            "data_classification": self.data_classification,
            "evidence_refs": list(self.evidence_refs),
            "items": list(self.items),
        }
        if self.denial_reason is not None:
            payload["denial_reason"] = self.denial_reason
        if self.needs_info_reason is not None:
            payload["needs_info_reason"] = self.needs_info_reason
        return payload


class ContextBundleBuilder:
    """Creates the server-side context contract for backend-owned policies."""

    def build(
        self,
        principal: Principal,
        *,
        request: ContextBuildRequest,
        correlation_id: str,
    ) -> ContextBundle:
        if not principal.active:
            return ContextBundle(
                status="DENIED",
                context_id="context_denied",
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
                division_id=request.division_id or principal.division_id,
                project_id=request.project_id or principal.project_id,
                denial_reason="The authenticated principal is not active.",
            )

        if not principal.scopes:
            return ContextBundle(
                status="NEEDS_INFO",
                context_id="context_needs_info",
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
                division_id=request.division_id or principal.division_id,
                project_id=request.project_id or principal.project_id,
                needs_info_reason="At least one Backend-authorized scope is required.",
                scope_refs=tuple(sorted(principal.scopes)),
            )

        scope_refs = tuple(sorted(principal.scopes))
        allowed_capabilities = tuple(sorted({*request.capability_ids, *principal.permissions}))
        allowed_tools = tuple(sorted(set(request.tool_ids)))
        budget = max(0, request.budget_hint or 0)
        token_limit = max(0, request.token_hint or 0)

        context_id = (
            f"context_{abs(hash((principal.actor_id, tuple(scope_refs), correlation_id))):x}"
        )
        return ContextBundle(
            status="ACTIVE",
            context_id=context_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            division_id=request.division_id or principal.division_id,
            project_id=request.project_id or principal.project_id,
            scope_refs=scope_refs,
            allowed_capabilities=allowed_capabilities,
            allowed_tools=allowed_tools,
            budget=budget,
            token_limit=token_limit,
            data_classification="INTERNAL",
            evidence_refs=(),
            items=(),
        )
