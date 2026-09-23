"""Typed source access requirement used to gate source expansion and classification."""

from __future__ import annotations

from dataclasses import dataclass

from alos.identity import Principal


@dataclass(frozen=True, slots=True)
class SourceRequirement:
    actor_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    scope_refs: tuple[str, ...] = ()
    division_id: str | None = None
    project_id: str | None = None
    source_classification: str = "INTERNAL"
    requested_scope: str | None = None
    is_valid: bool = True
    allows_external_sources: bool = True
    reason: str | None = None


class SourceRequirementBuilder:
    """Builds a backend-owned requirement from the authenticated principal and source request."""

    def build(
        self,
        principal: Principal,
        *,
        source_classification: str = "INTERNAL",
        requested_scope: str | None = None,
    ) -> SourceRequirement:
        classification = str(source_classification).upper()
        scope_refs = tuple(sorted(principal.scopes))
        external = classification in {"EXTERNAL", "UNTRUSTED"}
        if not principal.active or not scope_refs:
            return SourceRequirement(
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                scope_refs=scope_refs,
                division_id=principal.division_id,
                project_id=principal.project_id,
                source_classification=classification,
                requested_scope=requested_scope,
                is_valid=False,
                allows_external_sources=False,
                reason=(
                    "Source access requires an active principal with at least one authorized scope."
                ),
            )

        if external:
            return SourceRequirement(
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                scope_refs=scope_refs,
                division_id=principal.division_id,
                project_id=principal.project_id,
                source_classification=classification,
                requested_scope=requested_scope,
                is_valid=False,
                allows_external_sources=False,
                reason="External source scope expansion is forbidden.",
            )

        return SourceRequirement(
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            scope_refs=scope_refs,
            division_id=principal.division_id,
            project_id=principal.project_id,
            source_classification=classification,
            requested_scope=requested_scope,
            allows_external_sources=True,
        )
