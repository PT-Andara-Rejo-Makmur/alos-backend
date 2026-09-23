"""In-memory identity authority used by the backend as the canonical auth source of truth."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from alos.identity import Actor, DataScope, Membership, Organization, Principal, Tenant, Workspace
from alos.identity.directory import IdentityConflictError, IdentityDirectory
from alos.permissions import PermissionRegistry, RoleGrant
from alos.security.errors import PlatformError


@dataclass(frozen=True, slots=True)
class AccountRecord:
    email: str
    password_hash: str
    actor_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str


class AuthService:
    """Backend-owned source of truth for authenticating users and resolving principals."""

    def __init__(self) -> None:
        self._directory = IdentityDirectory()
        self._permissions = PermissionRegistry()
        self._accounts: dict[str, AccountRecord] = {}

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        email = str(payload.get("email", "")).strip().lower()
        if not email:
            raise PlatformError(
                "INVALID_EMAIL",
                "email is required",
                status_code=400,
            )
        if email in self._accounts:
            raise PlatformError(
                "USER_ALREADY_EXISTS",
                "user already exists",
                status_code=409,
            )

        tenant_id = str(payload.get("tenant_id") or "tenant_default")
        organization_id = str(payload.get("organization_id") or "org_default")
        workspace_id = str(payload.get("workspace_id") or "workspace_default")
        division_id = payload.get("division_id") or None
        project_id = payload.get("project_id") or None
        display_name = str(payload.get("display_name") or email.split("@", 1)[0])
        roles = frozenset(str(item) for item in payload.get("roles", []))
        permissions = frozenset(str(item) for item in payload.get("permissions", []))
        scopes = frozenset(str(item) for item in payload.get("scopes", []))
        data_scope_value = str(payload.get("data_scope", DataScope.OWN_ASSIGNED.value))

        try:
            self._directory.add_tenant(Tenant(tenant_id=tenant_id, name=tenant_id))
        except IdentityConflictError:
            pass
        try:
            self._directory.add_organization(
                Organization(
                    organization_id=organization_id,
                    tenant_id=tenant_id,
                    name=organization_id,
                )
            )
        except IdentityConflictError:
            pass
        try:
            self._directory.add_workspace(
                Workspace(
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    organization_id=organization_id,
                    name=workspace_id,
                )
            )
        except IdentityConflictError:
            pass

        actor_id = f"actor_{uuid.uuid4().hex[:12]}"
        try:
            self._directory.add_actor(
                Actor(
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                    organization_id=organization_id,
                    display_name=display_name,
                    division_id=str(division_id) if division_id is not None else None,
                    project_id=str(project_id) if project_id is not None else None,
                )
            )
        except IdentityConflictError as exc:
            raise PlatformError(
                "IDENTITY_CONFLICT",
                "actor identity could not be created",
                status_code=409,
                details={"reason": str(exc)},
            ) from exc

        try:
            self._directory.add_membership(
                Membership(
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    roles=roles,
                    permissions=permissions,
                    scopes=scopes,
                    data_scope=DataScope(data_scope_value),
                    division_id=str(division_id) if division_id is not None else None,
                    project_id=str(project_id) if project_id is not None else None,
                )
            )
        except (IdentityConflictError, ValueError) as exc:
            raise PlatformError(
                "INVALID_SCOPE_OR_IDENTITY",
                "membership is outside the backend identity boundary",
                status_code=400,
                details={"reason": str(exc)},
            ) from exc

        for role in roles:
            self._permissions.register(
                RoleGrant(
                    role_id=role,
                    tenant_id=tenant_id,
                    organization_id=organization_id,
                    permission_refs=permissions,
                    scope_refs=scopes,
                    active=True,
                )
            )

        password = str(payload.get("password") or "")
        if len(password) < 8:
            raise PlatformError(
                "WEAK_PASSWORD",
                "password must be at least 8 characters long",
                status_code=400,
            )
        account = AccountRecord(
            email=email,
            password_hash=self._hash_password(password),
            actor_id=actor_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        self._accounts[email] = account

        principal = self._directory.principal_for(actor_id, workspace_id)
        if principal is None:
            raise PlatformError(
                "PRINCIPAL_NOT_READY",
                "user principal could not be resolved",
                status_code=500,
            )
        return self._serialize_principal(principal, include_email=email, include_name=display_name)

    def login(self, email: str, password: str) -> dict[str, Any]:
        key = str(email or "").strip().lower()
        account = self._accounts.get(key)
        if account is None or not self._verify_password(password, account.password_hash):
            raise PlatformError(
                "INVALID_CREDENTIALS",
                "email or password is invalid",
                status_code=401,
            )

        principal = self._directory.principal_for(account.actor_id, account.workspace_id)
        if principal is None:
            raise PlatformError(
                "ACCOUNT_DISABLED",
                "account is not active in the configured workspace",
                status_code=403,
            )

        token = self._issue_token(account)
        body = self._serialize_principal(principal, include_email=account.email)
        body["token_type"] = "bearer"  # noqa: S105 - OAuth2 token type constant, not a password
        body["access_token"] = token
        return body

    def whoami(self, token: str) -> dict[str, Any]:
        if not token:
            raise PlatformError("MISSING_TOKEN", "authorization token is required", status_code=401)
        account = self._resolve_account_from_token(token)
        if account is None:
            raise PlatformError("INVALID_TOKEN", "token is invalid or expired", status_code=401)
        principal = self._directory.principal_for(account.actor_id, account.workspace_id)
        if principal is None:
            raise PlatformError(
                "ACCOUNT_DISABLED",
                "account is not active in the configured workspace",
                status_code=403,
            )
        return self._serialize_principal(principal, include_email=account.email)

    def _resolve_account_from_token(self, token: str) -> AccountRecord | None:
        for account in self._accounts.values():
            if self._issue_token(account) == token:
                return account
        return None

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            200_000,
        )
        return f"pbkdf2_sha256${salt}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        try:
            algorithm, salt, digest = stored_hash.split("$", 2)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            200_000,
        ).hex()
        return hmac.compare_digest(expected, digest)

    @staticmethod
    def _issue_token(account: AccountRecord) -> str:
        raw = f"{account.email}:{account.actor_id}:{account.workspace_id}:{account.tenant_id}"
        return "alos_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _serialize_principal(
        principal: Principal,
        *,
        include_email: str | None = None,
        include_name: str | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "actor_id": principal.actor_id,
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "division_id": principal.division_id,
            "project_id": principal.project_id,
            "permissions": sorted(principal.permissions),
            "scopes": sorted(principal.scopes),
            "roles": sorted(principal.roles),
            "data_scope": principal.data_scope.value,
            "active": principal.active,
        }
        if include_email is not None:
            response["email"] = include_email
        if include_name is not None:
            response["display_name"] = include_name
        return response
