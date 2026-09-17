"""Canonical identity context used by authorization decisions."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Principal:
    actor_id: str
    tenant_id: str
    workspace_id: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
