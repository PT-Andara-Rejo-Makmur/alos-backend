"""Persistent Backend authentication and canonical workspace projection service."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from alos.authentication.repository import (
    AccessState,
    AuthRepository,
    MembershipMutation,
    ProvisionAccount,
    SessionState,
)
from alos.security.errors import PlatformError

CANONICAL_ROLE_MAP = {
    "DIRECTOR": "EXECUTIVE",
    "DIVISION_LEAD": "WORKSPACE_LEAD",
    "DIVISION_OWNER": "WORKSPACE_LEAD",
    "DIVISION_MEMBER": "WORKSPACE_MEMBER",
    "MEMBER": "WORKSPACE_MEMBER",
    "IT_LEAD": "IT_ADMIN",
    "ADMIN": "IT_ADMIN",
    "QA_SECURITY": "QA_ASSURANCE",
}
CANONICAL_ROLES = frozenset(
    {
        "EXECUTIVE",
        "WORKSPACE_LEAD",
        "WORKSPACE_MEMBER",
        "BUSINESS_REVIEWER",
        "IT_ADMIN",
        "AI_ADMIN",
        "TECHNICAL_REVIEWER",
        "QA_ASSURANCE",
    }
)


class AuthService:
    """Authenticate accounts and resolve access exclusively through an injected repository."""

    def __init__(self, repository: AuthRepository, *, session_ttl_minutes: int = 480) -> None:
        self._repository = repository
        self._session_ttl = timedelta(minutes=session_ttl_minutes)

    async def register_for_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bootstrap synthetic identity only when the application explicitly enables it."""
        return await self._provision(payload, bootstrap=True)

    async def provision(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Provision an account into an existing Backend-owned authority boundary."""
        return await self._provision(payload, bootstrap=False)

    async def _provision(self, payload: dict[str, Any], *, bootstrap: bool) -> dict[str, Any]:
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password") or "")
        if not email:
            raise PlatformError("INVALID_EMAIL", "email is required", status_code=400)
        if len(password) < 8:
            raise PlatformError(
                "WEAK_PASSWORD", "password must be at least 8 characters long", status_code=400
            )
        role_refs = tuple(
            sorted(
                {
                    CANONICAL_ROLE_MAP.get(str(item).upper(), str(item).upper())
                    for item in (payload.get("role_refs") or payload.get("roles", []))
                }
            )
        )
        if not role_refs or not set(role_refs).issubset(CANONICAL_ROLES):
            raise PlatformError(
                "INVALID_AUTHORIZATION_ROLE",
                "role_refs must use the canonical authorization vocabulary",
                status_code=400,
            )
        workspace_id = str(payload.get("workspace_id") or "")
        workspace_key = str(payload.get("workspace_key") or workspace_id).upper().replace("-", "_")
        command = ProvisionAccount(
            actor_id=f"actor_{uuid.uuid4().hex}",
            email=email,
            password_hash=self._hash_password(password),
            display_name=str(payload.get("display_name") or email.split("@", 1)[0]),
            tenant_id=str(payload.get("tenant_id") or ""),
            organization_id=str(payload.get("organization_id") or ""),
            workspace_id=workspace_id,
            workspace_key=workspace_key,
            workspace_name=str(payload.get("workspace_name") or workspace_id),
            workspace_type=str(payload.get("workspace_type") or "BUSINESS"),
            organizational_unit_id=_optional(payload.get("organizational_unit_id")),
            division_code=_optional(payload.get("division_code")),
            role_refs=role_refs,
            permission_refs=tuple(
                sorted(
                    str(item)
                    for item in (payload.get("permission_refs") or payload.get("permissions", []))
                )
            ),
            scope_refs=tuple(
                sorted(
                    str(item) for item in (payload.get("scope_refs") or payload.get("scopes", []))
                )
            ),
            data_scope=str(payload.get("data_scope") or "OWN_ASSIGNED"),
        )
        if not all(
            (
                command.tenant_id,
                command.organization_id,
                command.workspace_id,
                command.workspace_key,
            )
        ):
            raise PlatformError(
                "INVALID_IDENTITY_BOUNDARY",
                "tenant, organization, and workspace are required",
                status_code=400,
            )
        try:
            account = await self._repository.provision(command, bootstrap=bootstrap)
        except ValueError as exc:
            message = str(exc)
            if "outside an active authority boundary" in message:
                raise PlatformError(
                    "AUTHORITY_BOUNDARY_CONFLICT", message, status_code=403
                ) from exc
            code = "USER_ALREADY_EXISTS" if "already exists" in message else "IDENTITY_CONFLICT"
            raise PlatformError(code, message, status_code=409) from exc
        accesses = await self._repository.active_access(account.actor_id)
        return self._account_projection(
            account.email,
            account.actor_id,
            account.tenant_id,
            account.organization_id,
            account.display_name,
            accesses,
        )

    async def login(self, email: str, password: str) -> dict[str, Any]:
        account = await self._repository.account_by_email(str(email or "").strip().lower())
        if (
            account is None
            or not account.active
            or not self._verify_password(password, account.password_hash)
        ):
            raise PlatformError(
                "INVALID_CREDENTIALS", "email or password is invalid", status_code=401
            )
        accesses = await self._repository.active_access(account.actor_id)
        if not accesses:
            raise PlatformError(
                "ACCOUNT_DISABLED", "account has no active workspace membership", status_code=403
            )
        issued_at = datetime.now(UTC)
        expires_at = issued_at + self._session_ttl
        raw_token = "alos_" + secrets.token_urlsafe(48)
        session = await self._repository.create_session(
            session_id=f"session_{uuid.uuid4().hex}",
            account=account,
            token_hash=self._token_hash(raw_token),
            active_workspace_id=accesses[0].workspace_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return {
            "access_token": raw_token,
            "token_type": "bearer",
            "principal": self._session_projection(session, accesses),
        }

    async def whoami(self, token: str) -> dict[str, Any]:
        session, accesses = await self._resolve_session(token)
        return self._session_projection(session, accesses)

    async def list_workspaces(self, token: str) -> list[dict[str, Any]]:
        _, accesses = await self._resolve_session(token)
        return [self._access_projection(access) for access in accesses]

    async def select_active_workspace(self, token: str, workspace_id: str) -> dict[str, Any]:
        session, accesses = await self._resolve_session(token)
        selected = next((item for item in accesses if item.workspace_id == workspace_id), None)
        if selected is None:
            raise PlatformError(
                "WORKSPACE_ACCESS_DENIED",
                "workspace is not authorized by an active membership",
                status_code=403,
            )
        await self._repository.set_active_workspace(session.session_id, workspace_id)
        return {
            "actor_id": session.account.actor_id,
            "organization_id": session.account.organization_id,
            "workspace": self._workspace_projection(selected),
            "membership": self._access_projection(selected),
        }

    async def logout(self, token: str) -> None:
        session, _ = await self._resolve_session(token)
        await self._repository.revoke_session(session.session_id, datetime.now(UTC))

    async def list_account_access(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> dict[str, Any]:
        try:
            accesses = await self._repository.all_access(
                actor_id, tenant_id=tenant_id, organization_id=organization_id
            )
        except ValueError as exc:
            raise PlatformError("IDENTITY_NOT_FOUND", str(exc), status_code=404) from exc
        return {
            "actor_id": actor_id,
            "workspace_access": [self._access_projection(access) for access in accesses],
        }

    async def assign_membership(
        self,
        actor_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        command = self._membership_command(
            actor_id, payload, tenant_id=tenant_id, organization_id=organization_id
        )
        try:
            access = await self._repository.assign_membership(command)
        except ValueError as exc:
            status = 409 if "already exists" in str(exc) else 404
            raise PlatformError("MEMBERSHIP_CONFLICT", str(exc), status_code=status) from exc
        return self._access_projection(access)

    async def update_membership(
        self,
        actor_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        command = self._membership_command(
            actor_id, payload, tenant_id=tenant_id, organization_id=organization_id
        )
        try:
            access = await self._repository.update_membership(command)
        except ValueError as exc:
            raise PlatformError("MEMBERSHIP_NOT_FOUND", str(exc), status_code=404) from exc
        return self._access_projection(access)

    async def revoke_membership(
        self,
        actor_id: str,
        workspace_id: str,
        *,
        tenant_id: str,
        organization_id: str,
    ) -> None:
        try:
            await self._repository.revoke_membership(
                actor_id,
                workspace_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                revoked_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise PlatformError("MEMBERSHIP_NOT_FOUND", str(exc), status_code=404) from exc

    async def set_account_active(
        self,
        actor_id: str,
        active: bool,
        *,
        tenant_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        try:
            account = await self._repository.set_account_active(
                actor_id,
                active,
                tenant_id=tenant_id,
                organization_id=organization_id,
                changed_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise PlatformError("IDENTITY_NOT_FOUND", str(exc), status_code=404) from exc
        return {"actor_id": account.actor_id, "active": account.active}

    async def _resolve_session(self, token: str) -> tuple[SessionState, list[AccessState]]:
        if not token:
            raise PlatformError("MISSING_TOKEN", "authorization token is required", status_code=401)
        session = await self._repository.session_by_token_hash(self._token_hash(token))
        if session is None:
            raise PlatformError(
                "INVALID_TOKEN", "token is invalid, expired, or revoked", status_code=401
            )
        accesses = await self._repository.active_access(session.account.actor_id)
        if not accesses:
            raise PlatformError(
                "ACCESS_REVOKED", "all workspace memberships are inactive", status_code=403
            )
        return session, accesses

    @staticmethod
    def _account_projection(
        email: str,
        actor_id: str,
        tenant_id: str,
        organization_id: str,
        display_name: str,
        accesses: list[AccessState],
    ) -> dict[str, Any]:
        return {
            "actor": {
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "organization_id": organization_id,
                "display_name": display_name,
                "active": True,
            },
            "email": email,
            "workspace_access": [AuthService._access_projection(item) for item in accesses],
        }

    @staticmethod
    def _session_projection(session: SessionState, accesses: list[AccessState]) -> dict[str, Any]:
        base = AuthService._account_projection(
            session.account.email,
            session.account.actor_id,
            session.account.tenant_id,
            session.account.organization_id,
            session.account.display_name,
            accesses,
        )
        active = next(
            (item for item in accesses if item.workspace_id == session.active_workspace_id), None
        )
        base.update(
            active_workspace=(AuthService._access_projection(active) if active else None),
            issued_at=session.issued_at.isoformat().replace("+00:00", "Z"),
            expires_at=session.expires_at.isoformat().replace("+00:00", "Z"),
        )
        return base

    @staticmethod
    def _access_projection(access: AccessState) -> dict[str, Any]:
        return {
            "workspace": AuthService._workspace_projection(access),
            "role_refs": list(access.role_refs),
            "permission_refs": list(access.permission_refs),
            "scope_refs": list(access.scope_refs),
            "data_scope": access.data_scope,
            "access_level": access.role_refs[0] if access.role_refs else None,
            "active": access.active,
        }

    @staticmethod
    def _membership_command(
        actor_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        organization_id: str,
    ) -> MembershipMutation:
        role_refs = tuple(
            sorted(
                {
                    CANONICAL_ROLE_MAP.get(str(item).upper(), str(item).upper())
                    for item in payload.get("role_refs", [])
                }
            )
        )
        if not role_refs or not set(role_refs).issubset(CANONICAL_ROLES):
            raise PlatformError(
                "INVALID_AUTHORIZATION_ROLE",
                "role_refs must use the canonical authorization vocabulary",
                status_code=400,
            )
        return MembershipMutation(
            actor_id=actor_id,
            workspace_id=str(payload.get("workspace_id") or ""),
            tenant_id=tenant_id,
            organization_id=organization_id,
            role_refs=role_refs,
            permission_refs=tuple(sorted(str(item) for item in payload.get("permission_refs", []))),
            scope_refs=tuple(sorted(str(item) for item in payload.get("scope_refs", []))),
            data_scope=str(payload.get("data_scope") or "OWN_ASSIGNED"),
        )

    @staticmethod
    def _workspace_projection(access: AccessState) -> dict[str, Any]:
        return {
            "workspace_id": access.workspace_id,
            "workspace_key": access.workspace_key,
            "organization_id": access.organization_id,
            "workspace_name": access.workspace_name,
            "workspace_type": access.workspace_type,
            "organizational_unit_id": access.organizational_unit_id,
            "division_code": access.division_code,
            "active": access.active,
        }

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
        return f"pbkdf2_sha256${salt}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        try:
            algorithm, salt, digest = stored_hash.split("$", 2)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
        return hmac.compare_digest(expected, digest)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


def _optional(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None
