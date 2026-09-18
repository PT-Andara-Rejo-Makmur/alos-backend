"""Public auth request/response models for the backend authority boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=3, max_length=128)
    organization_id: str = Field(min_length=3, max_length=128)
    workspace_id: str = Field(min_length=3, max_length=128)
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    data_scope: Literal["COMPANY", "DIVISION", "PROJECT", "OWN_ASSIGNED"] = "OWN_ASSIGNED"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=256)


class AuthPrincipalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    email: str | None = None
    display_name: str | None = None
    permissions: list[str]
    scopes: list[str]
    roles: list[str]
    data_scope: Literal["COMPANY", "DIVISION", "PROJECT", "OWN_ASSIGNED"]
    active: bool


class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    principal: AuthPrincipalResponse
