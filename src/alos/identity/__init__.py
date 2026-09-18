"""Canonical identity and tenancy records owned by ALOS Backend."""

from dataclasses import dataclass, field
from enum import StrEnum


class HumanRole(StrEnum):
    DIRECTOR = "DIRECTOR"
    DIVISION_LEAD = "DIVISION_LEAD"
    DIVISION_MEMBER = "DIVISION_MEMBER"
    IT_ADMIN = "IT_ADMIN"
    AI_ADMIN = "AI_ADMIN"
    TECHNICAL_REVIEWER = "TECHNICAL_REVIEWER"
    BUSINESS_REVIEWER = "BUSINESS_REVIEWER"
    QA_SECURITY = "QA_SECURITY"


class DataScope(StrEnum):
    COMPANY = "COMPANY"
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
    active: bool = True
