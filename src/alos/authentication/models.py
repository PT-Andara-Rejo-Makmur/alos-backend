"""Public request and canonical identity/access projection models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AuthorizationRole = Literal[
    "EXECUTIVE",
    "DIVISION_LEAD",
    "DIVISION_MEMBER",
    "IT_ADMIN",
]
IdentityDataScope = Literal[
    "COMPANY", "ORGANIZATIONAL_UNIT", "WORKSPACE", "PROJECT", "OWN_ASSIGNED"
]
WorkspaceType = Literal["EXECUTIVE", "BUSINESS", "IT_OPERATIONS", "GOVERNANCE", "SHARED"]


class RegisterRequest(BaseModel):
    """Development/test-only synthetic identity bootstrap request."""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=3, max_length=128)
    organization_id: str = Field(min_length=3, max_length=128)
    workspace_id: str = Field(min_length=3, max_length=128)
    workspace_key: str | None = Field(default=None, max_length=64)
    workspace_name: str | None = Field(default=None, max_length=200)
    workspace_type: WorkspaceType = "BUSINESS"
    organizational_unit_id: str | None = None
    division_code: str | None = None
    role_refs: list[str] = Field(default_factory=list)
    permission_refs: list[str] = Field(default_factory=list)
    scope_refs: list[str] = Field(default_factory=list)
    data_scope: IdentityDataScope = "OWN_ASSIGNED"
    # Temporary input aliases used only by the gated compatibility endpoint.
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class ProvisionAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str = Field(min_length=3, max_length=128)
    workspace_id: str = Field(min_length=3, max_length=128)
    role_refs: list[AuthorizationRole] = Field(min_length=1, max_length=1)
    effective_at: datetime
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class ProvisioningCandidateProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str
    employee_number: str | None = None
    full_name: str
    email: str | None = None
    department_code: str | None = None
    position_title: str | None = None
    employment_status: Literal["ACTIVE"]
    linkage_state: Literal["AVAILABLE"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=256)


class ActivateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)


class AccountStateChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=2000)


class ActorProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    tenant_id: str
    organization_id: str
    display_name: str
    active: bool


class WorkspaceProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    workspace_key: str
    organization_id: str
    workspace_name: str
    workspace_type: WorkspaceType
    organizational_unit_id: str | None = None
    division_code: str | None = None
    active: bool


class WorkspaceAccessProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: WorkspaceProjection
    role_refs: list[AuthorizationRole]
    permission_refs: list[str]
    scope_refs: list[str]
    data_scope: IdentityDataScope
    access_level: str | None = None
    active: bool


class AuthenticatedPrincipalProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: ActorProjection
    email: str
    workspace_access: list[WorkspaceAccessProjection]
    active_workspace: WorkspaceAccessProjection | None
    issued_at: str
    expires_at: str


class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    principal: AuthenticatedPrincipalProjection


class ActiveWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=128)


class ActiveWorkspaceProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    organization_id: str
    workspace: WorkspaceProjection
    membership: WorkspaceAccessProjection


class MembershipMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=128)
    role_refs: list[AuthorizationRole] = Field(min_length=1, max_length=1)
    effective_at: datetime
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class MembershipRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=2000)


class AccountAccessProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    workspace_access: list[WorkspaceAccessProjection]


class IdentityAccountProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    display_name: str
    email: str
    active: bool
    administrative_state: str = "ENABLED"
    activation_state: str = "ACTIVATED"
    primary_workspace_id: str | None = None
    employee_id: str | None = None
    employee_number: str | None = None
    position_title: str | None = None
    department_code: str | None = None
    employment_status: str | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    email_delivered: bool | None = None
    workspace_access: list[WorkspaceAccessProjection]


class AccountStateProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    active: bool


class AdminSessionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    issued_at: datetime
    expires_at: datetime
    active_workspace_id: str | None
    revoked: bool
    expired: bool = False
    last_activity_at: datetime | None = None


class IdentityAuditProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurred_at: datetime
    event_type: str
    entity_type: str
    entity_id: str
    workspace_id: str | None
    actor_id: str
    outcome: str


class ResendActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


class ResendActivationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    activation_state: Literal["PENDING"] = "PENDING"
    email_delivered: bool
    expires_at: datetime


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=255)


class PasswordResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)


class PasswordResetConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
