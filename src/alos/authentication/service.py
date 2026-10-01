"""Persistent Backend authentication and canonical workspace projection service."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from alos.authentication.email_address import normalize_email
from alos.authentication.repository import (
    AccessState,
    AuthRepository,
    MembershipMutation,
    ProvisionAccount,
    SessionState,
    WorkspaceState,
)
from alos.persistence.models import EmployeeRecord
from alos.security.errors import PlatformError

if TYPE_CHECKING:
    from alos.notifications.service import NotificationService

logger = logging.getLogger(__name__)

CANONICAL_ROLES = frozenset(
    {
        "EXECUTIVE",
        "DIVISION_LEAD",
        "DIVISION_MEMBER",
        "IT_ADMIN",
    }
)

DEFAULT_DOMAIN_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "hr": (
        "hr.read",
        "hr.write",
        "hr.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "finance": (
        "finance.read",
        "finance.write",
        "finance.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "sales": (
        "sales.read",
        "sales.write",
        "sales.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "marketing": (
        "marketing.read",
        "marketing.write",
        "marketing.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "legal": (
        "legal.read",
        "legal.write",
        "legal.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "property": (
        "property.read",
        "property.write",
        "property.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "it": (
        "it.read",
        "it.write",
        "it.delete",
        "work.read",
        "work.write",
        "navigation.read",
        "navigation.manage",
    ),
    "genesis": (
        "genesis.read",
        "genesis.write",
        "genesis.delete",
        "work.read",
        "work.write",
        "navigation.read",
    ),
    "shared": (
        "work.read",
        "work.write",
        "work.delete",
        "navigation.read",
    ),
}

ROLE_DEFAULT_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "IT_ADMIN": (
        "identity.accounts.manage",
        "identity.memberships.manage",
        "identity.memberships.read",
    ),
    "EXECUTIVE": (
        "navigation.read",
        "work.read",
    ),
}


def _resolve_default_permissions(
    division_code: str | None,
    workspace_type: str | None,
    role_refs: tuple[str, ...],
    provided_permissions: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    resolved = set(provided_permissions or [])
    domain = (division_code or "").strip().lower()
    if any(role in {"DIVISION_LEAD", "DIVISION_MEMBER"} for role in role_refs):
        resolved.update(DEFAULT_DOMAIN_PERMISSIONS.get(domain, ()))
    if "IT_ADMIN" in role_refs and workspace_type == "IT_OPERATIONS":
        resolved.update(DEFAULT_DOMAIN_PERMISSIONS["it"])
    for role in role_refs:
        if role in ROLE_DEFAULT_PERMISSIONS:
            resolved.update(ROLE_DEFAULT_PERMISSIONS[role])
    return tuple(sorted(str(item) for item in resolved))


class AuthService:
    """Authenticate accounts and resolve access exclusively through an injected repository."""

    def __init__(
        self,
        repository: AuthRepository,
        *,
        session_ttl_minutes: int = 480,
        activation_sink: Callable[[str, str], None] | None = None,
        notification_service: NotificationService | None = None,
        activation_ttl_hours: int = 24,
        password_reset_ttl_minutes: int = 60,
    ) -> None:
        self._repository = repository
        self._session_ttl = timedelta(minutes=session_ttl_minutes)
        self._activation_sink = activation_sink
        self._notification_service = notification_service
        self._activation_ttl = timedelta(hours=activation_ttl_hours)
        self._password_reset_ttl = timedelta(minutes=password_reset_ttl_minutes)

    async def register_for_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bootstrap synthetic identity only when the application explicitly enables it."""
        return await self._provision(payload, bootstrap=True)

    async def bootstrap_initial_admin(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create the first identity authority through an operator-only call path."""
        canonical = {
            **payload,
            "role_refs": ["IT_ADMIN"],
            "permission_refs": [
                "identity.accounts.manage",
                "identity.memberships.manage",
                "identity.memberships.read",
            ],
            "scope_refs": ["scope.identity.manage"],
            "data_scope": "COMPANY",
        }
        return await self._provision(
            canonical,
            bootstrap=True,
            initial_authority=True,
        )

    async def provision(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Provision an account into an existing Backend-owned authority boundary."""
        return await self._provision(payload, bootstrap=False)

    async def activate(
        self, token: str, password: str, password_confirmation: str
    ) -> dict[str, str]:
        if password != password_confirmation:
            raise PlatformError(
                "PASSWORD_CONFIRMATION_MISMATCH",
                "password confirmation does not match",
                status_code=422,
            )
        if len(password) < 8:
            raise PlatformError(
                "WEAK_PASSWORD",
                "password must be at least 8 characters long",
                status_code=400,
            )
        activated = await self._repository.activate_account(
            self._token_hash(token), self._hash_password(password), datetime.now(UTC)
        )
        if activated is None:
            raise PlatformError(
                "ACTIVATION_CHALLENGE_INVALID",
                "activation challenge is invalid, expired, or already used",
                status_code=422,
            )
        if self._notification_service is not None:
            try:
                await self._notification_service.send_activation_success(
                    to_email=activated.email,
                    employee_name=activated.display_name or activated.email,
                )
            except Exception:
                logger.debug("Failed sending activation success", exc_info=True)
        return {
            "actor_id": activated.actor_id,
            "activation_state": activated.activation_state,
            "tenant_id": activated.tenant_id,
            "organization_id": activated.organization_id,
            "workspace_id": activated.primary_workspace_id or "",
        }

    async def resend_activation(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> dict[str, Any]:
        new_token = secrets.token_urlsafe(40)
        token_hash = self._token_hash(new_token)
        expires_at = datetime.now(UTC) + self._activation_ttl
        try:
            (
                account,
                employee_name,
                workspace_name,
            ) = await self._repository.resend_activation_challenge(
                actor_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                new_token_hash=token_hash,
                new_expires_at=expires_at,
            )
        except ValueError as exc:
            status = 404 if "not found" in str(exc).lower() else 409
            raise PlatformError("ACTIVATION_RESEND_FAILED", str(exc), status_code=status) from exc

        accesses = await self._repository.active_access(actor_id)
        role_name = accesses[0].role_refs[0] if accesses and accesses[0].role_refs else "Anggota"
        email_delivered = False
        if self._notification_service is not None:
            try:
                delivery = await self._notification_service.send_activation_invitation(
                    to_email=account.email,
                    employee_name=employee_name or account.display_name or account.email,
                    workspace_name=workspace_name or "ALOS",
                    role_name=role_name,
                    activation_token=new_token,
                )
                email_delivered = delivery.success
            except Exception:
                email_delivered = False
        if self._activation_sink is not None:
            self._activation_sink(account.email, new_token)
        return {
            "actor_id": actor_id,
            "activation_state": "PENDING",
            "email_delivered": email_delivered,
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

    async def request_password_reset(self, email: str) -> dict[str, str]:
        cleaned_email = str(email or "").strip().lower()
        generic_message = "Jika email terdaftar, instruksi pemulihan telah dikirim."
        if not cleaned_email or "@" not in cleaned_email:
            return {"message": generic_message}

        reset_token = secrets.token_urlsafe(40)
        token_hash = self._token_hash(reset_token)
        expires_at = datetime.now(UTC) + self._password_reset_ttl

        result = await self._repository.create_password_reset_challenge(
            email=cleaned_email,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        if result is not None:
            account, display_name = result
            if self._notification_service is not None:
                try:
                    await self._notification_service.send_password_reset(
                        to_email=account.email,
                        employee_name=display_name or account.email,
                        reset_token=reset_token,
                    )
                except Exception:
                    logger.debug("Failed sending password reset email")
            if self._activation_sink is not None:
                self._activation_sink(account.email, reset_token)
        return {"message": generic_message}

    async def confirm_password_reset(
        self, token: str, password: str, password_confirmation: str
    ) -> dict[str, str]:
        if password != password_confirmation:
            raise PlatformError(
                "PASSWORD_CONFIRMATION_MISMATCH",
                "Kata sandi konfirmasi tidak sesuai",
                status_code=422,
            )
        if len(password) < 8:
            raise PlatformError(
                "WEAK_PASSWORD",
                "Kata sandi harus minimal 8 karakter",
                status_code=400,
            )
        token_hash = self._token_hash(token)
        password_hash = self._hash_password(password)
        account = await self._repository.confirm_password_reset(
            token_hash, password_hash, datetime.now(UTC)
        )
        if account is None:
            raise PlatformError(
                "RESET_TOKEN_INVALID",
                "Tautan pemulihan kata sandi tidak valid atau telah kedaluwarsa",
                status_code=422,
            )
        if self._notification_service is not None:
            try:
                await self._notification_service.send_password_changed(
                    to_email=account.email,
                    employee_name=account.display_name or account.email,
                )
            except Exception:
                logger.debug("Failed sending password changed email")
        return {
            "message": (
                "Kata sandi berhasil diperbarui. Silakan masuk menggunakan kata sandi baru Anda."
            )
        }

    async def import_employee(self, payload: dict[str, Any]) -> dict[str, Any]:
        employee_id = str(payload.get("employee_id") or "").strip()
        employee_number = str(payload.get("employee_number") or "").strip()
        full_name = str(payload.get("full_name") or "").strip()
        try:
            email = normalize_email(str(payload.get("email") or ""))
        except ValueError as exc:
            raise PlatformError("INVALID_EMPLOYEE_EMAIL", str(exc), status_code=422) from exc
        tenant_id = str(payload.get("tenant_id") or "").strip()
        organization_id = str(payload.get("organization_id") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        department_code = _optional(payload.get("department_code"))
        position_title = _optional(payload.get("position_title"))
        employment_status = str(payload.get("employment_status") or "ACTIVE").strip().upper()
        join_date_raw = payload.get("join_date")
        end_date_raw = payload.get("end_date")

        if not all(
            (employee_id, employee_number, full_name, tenant_id, organization_id, workspace_id)
        ):
            raise PlatformError(
                "INVALID_EMPLOYEE_PAYLOAD",
                (
                    "employee_id, employee_number, full_name, tenant_id, "
                    "organization_id, and workspace_id are required"
                ),
                status_code=400,
            )
        parsed_join = (
            date.fromisoformat(str(join_date_raw))
            if join_date_raw and not isinstance(join_date_raw, date)
            else join_date_raw
        )
        parsed_end = (
            date.fromisoformat(str(end_date_raw))
            if end_date_raw and not isinstance(end_date_raw, date)
            else end_date_raw
        )

        rec = EmployeeRecord(
            employee_id=employee_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=None,
            employee_number=employee_number,
            full_name=full_name,
            email=email,
            employment_status=employment_status,
            join_date=parsed_join,
            end_date=parsed_end,
            department_code=department_code,
            position_title=position_title,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        try:
            saved = await self._repository.import_employee(rec)
        except ValueError as exc:
            code = (
                "EMPLOYEE_CONFLICT"
                if "already exists" in str(exc)
                else "AUTHORITY_BOUNDARY_CONFLICT"
            )
            raise PlatformError(
                code, str(exc), status_code=409 if code == "EMPLOYEE_CONFLICT" else 403
            ) from exc
        return {
            "employee_id": saved.employee_id,
            "employee_number": saved.employee_number,
            "full_name": saved.full_name,
            "email": saved.email,
            "employment_status": saved.employment_status,
            "tenant_id": saved.tenant_id,
            "organization_id": saved.organization_id,
            "workspace_id": saved.workspace_id,
        }

    async def provisioning_candidates(
        self, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, str | None]]:
        return await self._repository.provisioning_candidates(
            tenant_id=tenant_id, organization_id=organization_id
        )

    async def _provision(
        self,
        payload: dict[str, Any],
        *,
        bootstrap: bool,
        initial_authority: bool = False,
    ) -> dict[str, Any]:
        if not bootstrap and "email" in payload:
            raise PlatformError(
                "CLIENT_EMAIL_FORBIDDEN", "account email is derived from Employee", status_code=422
            )
        email = str(payload.get("email", "")).strip().lower() if bootstrap else ""
        if bootstrap and not email:
            raise PlatformError("INVALID_EMAIL", "email is required", status_code=400)
        password = str(payload.get("password") or "") if bootstrap else secrets.token_urlsafe(48)
        if bootstrap and len(password) < 8:
            raise PlatformError(
                "WEAK_PASSWORD", "password must be at least 8 characters long", status_code=400
            )
        requested_roles = {
            str(item).upper() for item in (payload.get("role_refs") or payload.get("roles", []))
        }
        role_refs = tuple(sorted(requested_roles))
        if len(role_refs) != 1 or not set(role_refs).issubset(CANONICAL_ROLES):
            raise PlatformError(
                "INVALID_AUTHORIZATION_ROLE",
                "role_refs must contain exactly one canonical authorization role",
                status_code=400,
            )
        workspace_id = str(payload.get("workspace_id") or "")
        workspace: WorkspaceState | None = None
        if not bootstrap:
            workspace = await self._repository.workspace(workspace_id)
            if (
                workspace is None
                or not workspace.active
                or workspace.tenant_id != str(payload.get("tenant_id") or "")
                or workspace.organization_id != str(payload.get("organization_id") or "")
            ):
                raise PlatformError(
                    "AUTHORITY_BOUNDARY_CONFLICT",
                    "provisioning target is outside an active authority boundary",
                    status_code=403,
                )
        workspace_key = (
            workspace.workspace_key
            if workspace is not None
            else str(payload.get("workspace_key") or workspace_id).strip().lower()
        )
        division_code = (
            workspace.division_code
            if workspace is not None
            else _optional(payload.get("division_code"))
        )
        workspace_type = (
            workspace.workspace_type
            if workspace is not None
            else str(payload.get("workspace_type") or "BUSINESS")
        )
        if bootstrap and division_code is None:
            # Synthetic registration retains legacy test/bootstrap key inference only.
            bootstrap_key = workspace_key.lower()
            division_code = next(
                (
                    name.upper()
                    for name in DEFAULT_DOMAIN_PERMISSIONS
                    if bootstrap_key == name
                    or bootstrap_key.startswith(f"{name}_")
                    or bootstrap_key.endswith(f"_{name}")
                ),
                None,
            )
        effective_at = _as_utc(payload.get("effective_at")) or datetime.now(UTC)
        expires_at = _as_utc(payload.get("expires_at"))
        if expires_at is not None and expires_at <= effective_at:
            raise PlatformError(
                "INVALID_MEMBERSHIP_DATES",
                "expires_at must be later than effective_at",
                status_code=422,
            )
        activation_token = secrets.token_urlsafe(40) if not bootstrap else None
        activation_expires_at = datetime.now(UTC) + self._activation_ttl
        command = ProvisionAccount(
            actor_id=f"actor_{uuid.uuid4().hex}",
            email=email,
            password_hash=self._hash_password(password),
            display_name=(
                str(payload.get("display_name") or email.split("@", 1)[0]) if bootstrap else ""
            ),
            tenant_id=str(payload.get("tenant_id") or ""),
            organization_id=str(payload.get("organization_id") or ""),
            workspace_id=workspace_id,
            workspace_key=workspace_key,
            workspace_name=(
                workspace.workspace_name
                if workspace is not None
                else str(payload.get("workspace_name") or workspace_id)
            ),
            workspace_type=workspace_type,
            organizational_unit_id=(
                workspace.organizational_unit_id
                if workspace is not None
                else _optional(payload.get("organizational_unit_id"))
            ),
            division_code=division_code,
            role_refs=role_refs,
            permission_refs=_resolve_default_permissions(
                division_code,
                workspace_type if not initial_authority else None,
                role_refs,
                (payload.get("permission_refs") or payload.get("permissions", []))
                if bootstrap
                else None,
            ),
            scope_refs=tuple(
                sorted(
                    str(item)
                    for item in (
                        (payload.get("scope_refs") or payload.get("scopes", []))
                        if bootstrap
                        else []
                    )
                )
            ),
            data_scope=(
                str(payload.get("data_scope") or "OWN_ASSIGNED") if bootstrap else "WORKSPACE"
            ),
            employee_id=(str(payload.get("employee_id")) if not bootstrap else None),
            effective_at=effective_at,
            expires_at=expires_at,
            activation_token_hash=(
                self._token_hash(activation_token) if activation_token else None
            ),
            activation_expires_at=activation_expires_at,
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
            account = await self._repository.provision(
                command,
                bootstrap=bootstrap,
                initial_authority=initial_authority,
            )
        except ValueError as exc:
            message = str(exc)
            if "initial identity authority already exists" in message:
                raise PlatformError("IDENTITY_BOOTSTRAP_EXISTS", message, status_code=409) from exc
            if "outside an active authority boundary" in message:
                raise PlatformError(
                    "AUTHORITY_BOUNDARY_CONFLICT", message, status_code=403
                ) from exc
            code = "USER_ALREADY_EXISTS" if "already exists" in message else "IDENTITY_CONFLICT"
            raise PlatformError(code, message, status_code=409) from exc
        email_delivered: bool | None = None
        if not bootstrap and activation_token is not None:
            email_delivered = False
            if self._notification_service is not None:
                try:
                    delivery = await self._notification_service.send_activation_invitation(
                        to_email=account.email,
                        employee_name=account.display_name or account.email,
                        workspace_name=command.workspace_name,
                        role_name=role_refs[0],
                        activation_token=activation_token,
                    )
                    email_delivered = delivery.success
                except Exception:
                    email_delivered = False
            if self._activation_sink is not None:
                self._activation_sink(account.email, activation_token)
        accesses = await self._repository.active_access(account.actor_id)
        return {
            "actor_id": account.actor_id,
            "display_name": account.display_name,
            "email": account.email,
            "active": account.active,
            "administrative_state": account.administrative_state,
            "activation_state": account.activation_state,
            "primary_workspace_id": (
                account.primary_workspace_id
                if any(
                    access.active and access.workspace_id == account.primary_workspace_id
                    for access in accesses
                )
                else None
            ),
            "employee_id": account.employee_id or command.employee_id,
            "employee_number": account.employee_number,
            "department_code": account.department_code,
            "position_title": account.position_title,
            "employment_status": account.employment_status,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            "last_login_at": account.last_login_at.isoformat() if account.last_login_at else None,
            "email_delivered": email_delivered if not bootstrap else None,
            "workspace_access": [self._access_projection(access) for access in accesses],
        }

    async def login(self, email: str, password: str) -> dict[str, Any]:
        account = await self._repository.account_by_email(str(email or "").strip().lower())
        if (
            account is None
            or not account.active
            or account.administrative_state != "ENABLED"
            or account.activation_state != "ACTIVATED"
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
            active_workspace_id=(accesses[0].workspace_id if len(accesses) == 1 else None),
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

    async def list_organization_workspaces(
        self, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, Any]]:
        workspaces = await self._repository.list_organization_workspaces(
            tenant_id=tenant_id, organization_id=organization_id
        )
        return [self._workspace_catalog_projection(workspace) for workspace in workspaces]

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

    async def admin_sessions(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, object]]:
        try:
            return await self._repository.admin_sessions(
                actor_id, tenant_id=tenant_id, organization_id=organization_id
            )
        except ValueError as exc:
            raise PlatformError("IDENTITY_NOT_FOUND", str(exc), status_code=404) from exc

    async def revoke_actor_session(
        self, actor_id: str, session_id: str, *, tenant_id: str, organization_id: str
    ) -> None:
        try:
            await self._repository.revoke_actor_session(
                actor_id,
                session_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                revoked_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise PlatformError("SESSION_NOT_FOUND", str(exc), status_code=404) from exc

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

    async def list_accounts(self, *, tenant_id: str, organization_id: str) -> list[dict[str, Any]]:
        accounts = await self._repository.accounts(
            tenant_id=tenant_id, organization_id=organization_id
        )
        result: list[dict[str, Any]] = []
        for account in accounts:
            accesses = await self._repository.all_access(
                account.actor_id, tenant_id=tenant_id, organization_id=organization_id
            )
            result.append(
                {
                    "actor_id": account.actor_id,
                    "display_name": account.display_name,
                    "email": account.email,
                    "active": account.active,
                    "administrative_state": account.administrative_state,
                    "activation_state": account.activation_state,
                    "primary_workspace_id": (
                        account.primary_workspace_id
                        if any(
                            access.active and access.workspace_id == account.primary_workspace_id
                            for access in accesses
                        )
                        else None
                    ),
                    "employee_id": account.employee_id,
                    "employee_number": account.employee_number,
                    "position_title": account.position_title,
                    "department_code": account.department_code,
                    "employment_status": account.employment_status,
                    "created_at": account.created_at.isoformat() if account.created_at else None,
                    "last_login_at": account.last_login_at.isoformat()
                    if account.last_login_at
                    else None,
                    "email_delivered": account.email_delivered,
                    "workspace_access": [self._access_projection(access) for access in accesses],
                }
            )
        return result

    async def assign_membership(
        self,
        actor_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        command = await self._membership_command(
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
        command = await self._membership_command(
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

        if self._notification_service is not None:
            try:
                if not active:
                    await self._notification_service.send_account_suspended(
                        to_email=account.email,
                        employee_name=account.display_name or account.email,
                    )
                else:
                    await self._notification_service.send_account_reactivated(
                        to_email=account.email,
                        employee_name=account.display_name or account.email,
                    )
            except Exception:
                logger.debug("Failed sending account state notification", exc_info=True)
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

    async def _membership_command(
        self,
        actor_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        organization_id: str,
    ) -> MembershipMutation:
        role_refs = tuple(sorted({str(item).upper() for item in payload.get("role_refs", [])}))
        if len(role_refs) != 1 or not set(role_refs).issubset(CANONICAL_ROLES):
            raise PlatformError(
                "INVALID_AUTHORIZATION_ROLE",
                "role_refs must contain exactly one canonical authorization role",
                status_code=400,
            )
        workspace_id = str(payload.get("workspace_id") or "")
        workspace = await self._repository.workspace(workspace_id)
        if (
            workspace is None
            or not workspace.active
            or workspace.tenant_id != tenant_id
            or workspace.organization_id != organization_id
        ):
            raise PlatformError(
                "AUTHORITY_BOUNDARY_CONFLICT",
                "membership target is outside an active authority boundary",
                status_code=403,
            )
        return MembershipMutation(
            actor_id=actor_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            role_refs=role_refs,
            permission_refs=_resolve_default_permissions(
                workspace.division_code,
                workspace.workspace_type,
                role_refs,
                None,
            ),
            scope_refs=(),
            data_scope="WORKSPACE",
            effective_at=_as_utc(payload.get("effective_at")) or datetime.now(UTC),
            expires_at=_as_utc(payload.get("expires_at")),
            note=_optional(payload.get("note")),
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
    def _workspace_catalog_projection(workspace: WorkspaceState) -> dict[str, Any]:
        return {
            "workspace_id": workspace.workspace_id,
            "workspace_key": workspace.workspace_key,
            "organization_id": workspace.organization_id,
            "workspace_name": workspace.workspace_name,
            "workspace_type": workspace.workspace_type,
            "organizational_unit_id": workspace.organizational_unit_id,
            "division_code": workspace.division_code,
            "active": workspace.active,
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


def _as_utc(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
