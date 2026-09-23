"""Canonical identity and tenancy records owned by ALOS Backend."""

from dataclasses import dataclass, field
from enum import StrEnum


class HumanRole(StrEnum):
    EXECUTIVE = "EXECUTIVE"
    WORKSPACE_LEAD = "WORKSPACE_LEAD"
    WORKSPACE_MEMBER = "WORKSPACE_MEMBER"
    IT_ADMIN = "IT_ADMIN"
    AI_ADMIN = "AI_ADMIN"
    TECHNICAL_REVIEWER = "TECHNICAL_REVIEWER"
    BUSINESS_REVIEWER = "BUSINESS_REVIEWER"
    QA_ASSURANCE = "QA_ASSURANCE"


class DataScope(StrEnum):
    COMPANY = "COMPANY"
    ORGANIZATIONAL_UNIT = "ORGANIZATIONAL_UNIT"
    WORKSPACE = "WORKSPACE"
    DIVISION = "DIVISION"
    PROJECT = "PROJECT"
    OWN_ASSIGNED = "OWN_ASSIGNED"


@dataclass(frozen=True, slots=True)
class Principal:
    actor_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    data_scope: DataScope = DataScope.OWN_ASSIGNED
    division_id: str | None = None
    project_id: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    name: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class Organization:
    organization_id: str
    tenant_id: str
    name: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class Workspace:
    workspace_id: str
    tenant_id: str
    organization_id: str
    name: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    tenant_id: str
    organization_id: str
    display_name: str
    division_id: str | None = None
    project_id: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class Membership:
    actor_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    data_scope: DataScope = DataScope.OWN_ASSIGNED
    division_id: str | None = None
    project_id: str | None = None
    active: bool = True
