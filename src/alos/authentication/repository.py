"""Authentication persistence ports and production PostgreSQL implementation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.persistence.models import (
    ActivationChallengeRecord,
    ActorRecord,
    AuthAccountRecord,
    AuthSessionRecord,
    EmployeeRecord,
    OrganizationRecord,
    PasswordResetChallengeRecord,
    TenantRecord,
    WorkspaceMembershipRecord,
    WorkspaceRecord,
)


@dataclass(frozen=True, slots=True)
class AccountState:
    account_id: int
    email: str
    password_hash: str
    actor_id: str
    tenant_id: str
    organization_id: str
    display_name: str
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


@dataclass(frozen=True, slots=True)
class SessionState:
    session_id: str
    account: AccountState
    active_workspace_id: str | None
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AccessState:
    workspace_id: str
    workspace_key: str
    workspace_name: str
    workspace_type: str
    organization_id: str
    organizational_unit_id: str | None
    division_code: str | None
    role_refs: tuple[str, ...]
    permission_refs: tuple[str, ...]
    scope_refs: tuple[str, ...]
    data_scope: str
    active: bool


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    workspace_id: str
    workspace_key: str
    workspace_name: str
    workspace_type: str
    tenant_id: str
    organization_id: str
    organizational_unit_id: str | None
    division_code: str | None
    active: bool


@dataclass(frozen=True, slots=True)
class ProvisionAccount:
    actor_id: str
    email: str
    password_hash: str
    display_name: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    workspace_key: str
    workspace_name: str
    workspace_type: str
    organizational_unit_id: str | None
    division_code: str | None
    role_refs: tuple[str, ...]
    permission_refs: tuple[str, ...]
    scope_refs: tuple[str, ...]
    data_scope: str
    employee_id: str | None = None
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    activation_token_hash: str | None = None
    activation_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MembershipMutation:
    actor_id: str
    workspace_id: str
    tenant_id: str
    organization_id: str
    role_refs: tuple[str, ...]
    permission_refs: tuple[str, ...]
    scope_refs: tuple[str, ...]
    data_scope: str
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    note: str | None = None


class AuthRepository(Protocol):
    async def provision(
        self,
        command: ProvisionAccount,
        *,
        bootstrap: bool,
        initial_authority: bool = False,
    ) -> AccountState: ...

    async def account_by_email(self, email: str) -> AccountState | None: ...

    async def active_access(self, actor_id: str) -> list[AccessState]: ...

    async def all_access(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[AccessState]: ...

    async def accounts(self, *, tenant_id: str, organization_id: str) -> list[AccountState]: ...

    async def provisioning_candidates(
        self, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, str | None]]: ...

    async def workspace(self, workspace_id: str) -> WorkspaceState | None: ...

    async def list_organization_workspaces(
        self, *, tenant_id: str, organization_id: str
    ) -> list[WorkspaceState]: ...

    async def assign_membership(self, command: MembershipMutation) -> AccessState: ...

    async def update_membership(self, command: MembershipMutation) -> AccessState: ...

    async def revoke_membership(
        self,
        actor_id: str,
        workspace_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None: ...

    async def set_account_active(
        self,
        actor_id: str,
        active: bool,
        *,
        tenant_id: str,
        organization_id: str,
        changed_at: datetime,
    ) -> AccountState: ...

    async def create_session(
        self,
        *,
        session_id: str,
        account: AccountState,
        token_hash: str,
        active_workspace_id: str | None,
        issued_at: datetime,
        expires_at: datetime,
    ) -> SessionState: ...

    async def session_by_token_hash(self, token_hash: str) -> SessionState | None: ...

    async def activate_account(
        self, token_hash: str, password_hash: str, activated_at: datetime
    ) -> AccountState | None: ...

    async def set_active_workspace(self, session_id: str, workspace_id: str) -> None: ...

    async def revoke_session(self, session_id: str, revoked_at: datetime) -> None: ...

    async def admin_sessions(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, object]]: ...

    async def revoke_actor_session(
        self,
        actor_id: str,
        session_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None: ...

    async def import_employee(self, employee: EmployeeRecord) -> EmployeeRecord: ...

    async def resend_activation_challenge(
        self,
        actor_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        new_token_hash: str,
        new_expires_at: datetime,
    ) -> tuple[AccountState, str | None, str | None]: ...

    async def create_password_reset_challenge(
        self,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> tuple[AccountState, str] | None: ...

    async def confirm_password_reset(
        self,
        token_hash: str,
        new_password_hash: str,
        reset_at: datetime,
    ) -> AccountState | None: ...

    async def revoke_all_sessions(
        self,
        actor_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None: ...


class SqlAuthRepository:
    """PostgreSQL-backed authority for accounts, sessions, and workspace memberships."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def provision(
        self,
        command: ProvisionAccount,
        *,
        bootstrap: bool,
        initial_authority: bool = False,
    ) -> AccountState:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            if initial_authority:
                await session.execute(text("SELECT pg_advisory_xact_lock(1095515237)"))
                memberships = (
                    await session.scalars(
                        select(WorkspaceMembershipRecord).where(
                            WorkspaceMembershipRecord.active.is_(True),
                            WorkspaceMembershipRecord.revoked_at.is_(None),
                        )
                    )
                ).all()
                if any(
                    "IT_ADMIN" in membership.roles
                    or "identity.accounts.manage" in membership.permission_refs
                    for membership in memberships
                ):
                    raise ValueError("initial identity authority already exists")
            if await self._account_record(session, command.email) is not None:
                raise ValueError("account already exists")
            employee = None
            if not bootstrap:
                employee = await session.scalar(
                    select(EmployeeRecord)
                    .where(
                        EmployeeRecord.employee_id == command.employee_id,
                        EmployeeRecord.tenant_id == command.tenant_id,
                        EmployeeRecord.organization_id == command.organization_id,
                    )
                    .with_for_update()
                )
                if (
                    employee is None
                    or employee.actor_id is not None
                    or employee.employment_status != "ACTIVE"
                    or (employee.join_date is not None and employee.join_date > now.date())
                    or (employee.end_date is not None and employee.end_date < now.date())
                ):
                    raise ValueError("employee is not eligible for account provisioning")
            tenant = await session.get(TenantRecord, command.tenant_id)
            organization = await session.get(OrganizationRecord, command.organization_id)
            workspace = await session.get(WorkspaceRecord, command.workspace_id)
            if bootstrap:
                if tenant is None:
                    tenant = TenantRecord(
                        tenant_id=command.tenant_id, name=command.tenant_id, active=True
                    )
                    session.add(tenant)
                    await session.flush()
                if organization is None:
                    organization = OrganizationRecord(
                        organization_id=command.organization_id,
                        tenant_id=command.tenant_id,
                        name=command.organization_id,
                        active=True,
                    )
                    session.add(organization)
                    await session.flush()
                if workspace is None:
                    workspace = WorkspaceRecord(
                        workspace_id=command.workspace_id,
                        workspace_key=command.workspace_key,
                        tenant_id=command.tenant_id,
                        organization_id=command.organization_id,
                        name=command.workspace_name,
                        workspace_type=command.workspace_type,
                        organizational_unit_id=command.organizational_unit_id,
                        division_code=command.division_code,
                        active=True,
                    )
                    session.add(workspace)
                    await session.flush()
            if (
                tenant is None
                or organization is None
                or workspace is None
                or not tenant.active
                or not organization.active
                or not workspace.active
                or organization.tenant_id != command.tenant_id
                or workspace.tenant_id != command.tenant_id
                or workspace.organization_id != command.organization_id
            ):
                raise ValueError("provisioning target is outside an active authority boundary")
            actor = ActorRecord(
                actor_id=command.actor_id,
                tenant_id=command.tenant_id,
                organization_id=command.organization_id,
                display_name=employee.full_name if employee is not None else command.display_name,
                active=True,
            )
            session.add(actor)
            await session.flush()
            session.add(
                WorkspaceMembershipRecord(
                    actor_id=command.actor_id,
                    workspace_id=command.workspace_id,
                    tenant_id=command.tenant_id,
                    organization_id=command.organization_id,
                    roles=list(command.role_refs),
                    permission_refs=list(command.permission_refs),
                    scope_refs=list(command.scope_refs),
                    data_scope=command.data_scope,
                    active=True,
                    created_at=now,
                    effective_at=command.effective_at or now,
                    expires_at=command.expires_at,
                    updated_at=now,
                    revoked_at=None,
                )
            )
            await session.flush()
            account = AuthAccountRecord(
                email=command.email,
                password_hash=command.password_hash,
                actor_id=command.actor_id,
                tenant_id=command.tenant_id,
                organization_id=command.organization_id,
                legacy_workspace_id=None,
                display_name=employee.full_name if employee is not None else command.display_name,
                created_at=now,
                updated_at=now,
                active=True,
                primary_workspace_id=(
                    command.workspace_id
                    if (command.effective_at or now) <= now
                    and (command.expires_at is None or command.expires_at > now)
                    else None
                ),
                administrative_state="ENABLED",
                activation_state="PENDING" if not bootstrap else "ACTIVATED",
                activated_at=now if bootstrap else None,
            )
            session.add(account)
            await session.flush()
            if employee is not None:
                employee.actor_id = command.actor_id
            if not bootstrap and command.activation_token_hash and command.activation_expires_at:
                session.add(
                    ActivationChallengeRecord(
                        challenge_id=f"activation_{command.actor_id}",
                        account_id=account.account_id,
                        token_hash=command.activation_token_hash,
                        created_at=now,
                        expires_at=command.activation_expires_at,
                        consumed_at=None,
                    )
                )
            await session.flush()
            return replace(
                _account_state(account),
                employee_id=employee.employee_id if employee else None,
                employee_number=employee.employee_number if employee else None,
                position_title=employee.position_title if employee else None,
                department_code=employee.department_code if employee else None,
                employment_status=employee.employment_status if employee else None,
                created_at=now,
            )

    async def account_by_email(self, email: str) -> AccountState | None:
        async with self._session_factory() as session:
            record = await self._account_record(session, email)
            return _account_state(record) if record is not None else None

    async def active_access(self, actor_id: str) -> list[AccessState]:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            statement = (
                select(WorkspaceMembershipRecord, WorkspaceRecord, ActorRecord)
                .join(
                    WorkspaceRecord,
                    WorkspaceRecord.workspace_id == WorkspaceMembershipRecord.workspace_id,
                )
                .join(ActorRecord, ActorRecord.actor_id == WorkspaceMembershipRecord.actor_id)
                .where(
                    WorkspaceMembershipRecord.actor_id == actor_id,
                    WorkspaceMembershipRecord.active.is_(True),
                    WorkspaceMembershipRecord.revoked_at.is_(None),
                    WorkspaceMembershipRecord.effective_at <= now,
                    (
                        WorkspaceMembershipRecord.expires_at.is_(None)
                        | (WorkspaceMembershipRecord.expires_at > now)
                    ),
                    WorkspaceRecord.active.is_(True),
                    ActorRecord.active.is_(True),
                )
                .order_by(WorkspaceRecord.workspace_key)
            )
            rows = (await session.execute(statement)).all()
            return [_access_state(membership, workspace) for membership, workspace, _ in rows]

    async def all_access(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[AccessState]:
        async with self._session_factory() as session:
            actor = await session.get(ActorRecord, actor_id)
            if (
                actor is None
                or actor.tenant_id != tenant_id
                or actor.organization_id != organization_id
            ):
                raise ValueError("actor is outside the authority boundary")
            statement = (
                select(WorkspaceMembershipRecord, WorkspaceRecord)
                .join(
                    WorkspaceRecord,
                    WorkspaceRecord.workspace_id == WorkspaceMembershipRecord.workspace_id,
                )
                .where(WorkspaceMembershipRecord.actor_id == actor_id)
                .order_by(WorkspaceRecord.workspace_key)
            )
            rows = (await session.execute(statement)).all()
            return [_access_state(membership, workspace) for membership, workspace in rows]

    async def accounts(self, *, tenant_id: str, organization_id: str) -> list[AccountState]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AuthAccountRecord)
                .where(
                    AuthAccountRecord.tenant_id == tenant_id,
                    AuthAccountRecord.organization_id == organization_id,
                )
                .order_by(AuthAccountRecord.email)
            )
            result: list[AccountState] = []
            for record in rows.all():
                employee = await session.scalar(
                    select(EmployeeRecord).where(EmployeeRecord.actor_id == record.actor_id)
                )
                last_login_at = await session.scalar(
                    select(AuthSessionRecord.last_activity_at)
                    .where(AuthSessionRecord.actor_id == record.actor_id)
                    .order_by(AuthSessionRecord.last_activity_at.desc())
                    .limit(1)
                )
                activation = await session.scalar(
                    select(ActivationChallengeRecord)
                    .where(ActivationChallengeRecord.account_id == record.account_id)
                    .order_by(ActivationChallengeRecord.created_at.desc())
                    .limit(1)
                )
                activation_state = record.activation_state
                if (
                    activation_state == "PENDING"
                    and activation is not None
                    and activation.expires_at <= datetime.now(UTC)
                ):
                    activation_state = "EXPIRED"
                result.append(
                    replace(
                        _account_state(record),
                        employee_id=employee.employee_id if employee else None,
                        employee_number=employee.employee_number if employee else None,
                        position_title=employee.position_title if employee else None,
                        department_code=employee.department_code if employee else None,
                        employment_status=employee.employment_status if employee else None,
                        created_at=_utc(record.created_at),
                        last_login_at=_utc(last_login_at) if last_login_at else None,
                        activation_state=activation_state,
                    )
                )
            return result

    async def provisioning_candidates(
        self, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, str | None]]:
        today = datetime.now(UTC).date()
        async with self._session_factory() as session:
            employees = await session.scalars(
                select(EmployeeRecord)
                .where(
                    EmployeeRecord.tenant_id == tenant_id,
                    EmployeeRecord.organization_id == organization_id,
                    EmployeeRecord.actor_id.is_(None),
                    EmployeeRecord.employment_status == "ACTIVE",
                    (EmployeeRecord.join_date.is_(None) | (EmployeeRecord.join_date <= today)),
                    (EmployeeRecord.end_date.is_(None) | (EmployeeRecord.end_date >= today)),
                )
                .order_by(EmployeeRecord.full_name, EmployeeRecord.employee_number)
            )
            return [
                {
                    "employee_id": employee.employee_id,
                    "employee_number": employee.employee_number,
                    "full_name": employee.full_name,
                    "email": employee.email,
                    "department_code": employee.department_code,
                    "position_title": employee.position_title,
                    "employment_status": employee.employment_status,
                    "linkage_state": "AVAILABLE",
                }
                for employee in employees.all()
            ]

    async def workspace(self, workspace_id: str) -> WorkspaceState | None:
        async with self._session_factory() as session:
            record = await session.get(WorkspaceRecord, workspace_id)
            return _workspace_state(record) if record is not None else None

    async def list_organization_workspaces(
        self, *, tenant_id: str, organization_id: str
    ) -> list[WorkspaceState]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(WorkspaceRecord)
                .where(
                    WorkspaceRecord.tenant_id == tenant_id,
                    WorkspaceRecord.organization_id == organization_id,
                    WorkspaceRecord.active.is_(True),
                )
                .order_by(WorkspaceRecord.workspace_key)
            )
            return [_workspace_state(record) for record in rows.all()]

    async def assign_membership(self, command: MembershipMutation) -> AccessState:
        async with self._session_factory() as session, session.begin():
            actor, workspace = await self._membership_boundary(session, command)
            del actor
            existing = await session.get(
                WorkspaceMembershipRecord, (command.actor_id, command.workspace_id)
            )
            if existing is not None:
                raise ValueError("workspace membership already exists")
            membership = WorkspaceMembershipRecord(
                actor_id=command.actor_id,
                workspace_id=command.workspace_id,
                tenant_id=command.tenant_id,
                organization_id=command.organization_id,
                roles=list(command.role_refs),
                permission_refs=list(command.permission_refs),
                scope_refs=list(command.scope_refs),
                data_scope=command.data_scope,
                active=True,
                created_at=datetime.now(UTC),
                effective_at=command.effective_at or datetime.now(UTC),
                expires_at=command.expires_at,
                updated_at=datetime.now(UTC),
                revoked_at=None,
            )
            session.add(membership)
            await session.flush()
            return _access_state(membership, workspace)

    async def update_membership(self, command: MembershipMutation) -> AccessState:
        async with self._session_factory() as session, session.begin():
            _, workspace = await self._membership_boundary(session, command)
            membership = await session.get(
                WorkspaceMembershipRecord, (command.actor_id, command.workspace_id)
            )
            if membership is None or not membership.active or membership.revoked_at is not None:
                raise ValueError("active workspace membership does not exist")
            membership.roles = list(command.role_refs)
            membership.permission_refs = list(command.permission_refs)
            membership.scope_refs = list(command.scope_refs)
            membership.data_scope = command.data_scope
            membership.effective_at = command.effective_at or datetime.now(UTC)
            membership.expires_at = command.expires_at
            membership.updated_at = datetime.now(UTC)
            await session.flush()
            return _access_state(membership, workspace)

    async def revoke_membership(
        self,
        actor_id: str,
        workspace_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None:
        command = MembershipMutation(
            actor_id, workspace_id, tenant_id, organization_id, (), (), (), "OWN_ASSIGNED"
        )
        async with self._session_factory() as session, session.begin():
            await self._membership_boundary(session, command)
            membership = await session.get(WorkspaceMembershipRecord, (actor_id, workspace_id))
            if membership is None or not membership.active:
                raise ValueError("active workspace membership does not exist")
            membership.active = False
            membership.revoked_at = revoked_at
            account = await session.scalar(
                select(AuthAccountRecord).where(AuthAccountRecord.actor_id == actor_id)
            )
            if account is not None and account.primary_workspace_id == workspace_id:
                account.primary_workspace_id = None
            sessions = (
                await session.scalars(
                    select(AuthSessionRecord).where(
                        AuthSessionRecord.actor_id == actor_id,
                        AuthSessionRecord.active_workspace_id == workspace_id,
                    )
                )
            ).all()
            for auth_session in sessions:
                auth_session.active_workspace_id = None

    async def set_account_active(
        self,
        actor_id: str,
        active: bool,
        *,
        tenant_id: str,
        organization_id: str,
        changed_at: datetime,
    ) -> AccountState:
        async with self._session_factory() as session, session.begin():
            actor = await session.get(ActorRecord, actor_id)
            account = (
                await session.execute(
                    select(AuthAccountRecord).where(AuthAccountRecord.actor_id == actor_id)
                )
            ).scalar_one_or_none()
            if (
                actor is None
                or account is None
                or actor.tenant_id != tenant_id
                or actor.organization_id != organization_id
            ):
                raise ValueError("account is outside the authority boundary")
            actor.active = active
            account.active = active
            account.administrative_state = "ENABLED" if active else "SUSPENDED"
            account.updated_at = changed_at
            if not active:
                sessions = (
                    await session.scalars(
                        select(AuthSessionRecord).where(AuthSessionRecord.actor_id == actor_id)
                    )
                ).all()
                for auth_session in sessions:
                    auth_session.active = False
                    auth_session.revoked_at = changed_at
            await session.flush()
            return _account_state(account)

    async def create_session(
        self,
        *,
        session_id: str,
        account: AccountState,
        token_hash: str,
        active_workspace_id: str | None,
        issued_at: datetime,
        expires_at: datetime,
    ) -> SessionState:
        async with self._session_factory() as session, session.begin():
            record = AuthSessionRecord(
                session_id=session_id,
                account_id=account.account_id,
                actor_id=account.actor_id,
                tenant_id=account.tenant_id,
                organization_id=account.organization_id,
                legacy_workspace_id=None,
                active_workspace_id=active_workspace_id,
                token_hash=token_hash,
                issued_at=issued_at,
                expires_at=expires_at,
                last_activity_at=issued_at,
                revoked_at=None,
                active=True,
            )
            session.add(record)
        return SessionState(session_id, account, active_workspace_id, issued_at, expires_at)

    async def session_by_token_hash(self, token_hash: str) -> SessionState | None:
        async with self._session_factory() as session, session.begin():
            statement = (
                select(AuthSessionRecord, AuthAccountRecord, ActorRecord)
                .join(
                    AuthAccountRecord, AuthAccountRecord.account_id == AuthSessionRecord.account_id
                )
                .join(ActorRecord, ActorRecord.actor_id == AuthSessionRecord.actor_id)
                .where(
                    AuthSessionRecord.token_hash == token_hash,
                    AuthSessionRecord.active.is_(True),
                    AuthSessionRecord.revoked_at.is_(None),
                    AuthAccountRecord.active.is_(True),
                    ActorRecord.active.is_(True),
                )
            )
            row = (await session.execute(statement)).first()
            if row is None:
                return None
            current, account, _ = row
            now = datetime.now(UTC)
            expires_at = _utc(current.expires_at)
            if expires_at <= now:
                return None
            current.last_activity_at = now
            return SessionState(
                current.session_id,
                _account_state(account),
                current.active_workspace_id,
                _utc(current.issued_at),
                expires_at,
            )

    async def activate_account(
        self, token_hash: str, password_hash: str, activated_at: datetime
    ) -> AccountState | None:
        async with self._session_factory() as session, session.begin():
            challenge = await session.scalar(
                select(ActivationChallengeRecord)
                .where(
                    ActivationChallengeRecord.token_hash == token_hash,
                    ActivationChallengeRecord.consumed_at.is_(None),
                    ActivationChallengeRecord.expires_at > activated_at,
                )
                .with_for_update()
            )
            if challenge is None:
                return None
            account = await session.get(AuthAccountRecord, challenge.account_id)
            if account is None or account.activation_state != "PENDING":
                return None
            account.password_hash = password_hash
            account.activation_state = "ACTIVATED"
            account.activated_at = activated_at
            account.updated_at = activated_at
            challenge.consumed_at = activated_at
            await session.flush()
            return _account_state(account)

    async def set_active_workspace(self, session_id: str, workspace_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            record = await session.get(AuthSessionRecord, session_id)
            if record is None or not record.active or record.revoked_at is not None:
                raise ValueError("session is not active")
            record.active_workspace_id = workspace_id

    async def revoke_session(self, session_id: str, revoked_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            record = await session.get(AuthSessionRecord, session_id)
            if record is not None:
                record.active = False
                record.revoked_at = revoked_at

    async def admin_sessions(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            actor = await session.get(ActorRecord, actor_id)
            if (
                actor is None
                or actor.tenant_id != tenant_id
                or actor.organization_id != organization_id
            ):
                raise ValueError("actor is outside the authority boundary")
            rows = await session.scalars(
                select(AuthSessionRecord)
                .where(AuthSessionRecord.actor_id == actor_id)
                .order_by(AuthSessionRecord.issued_at.desc())
            )
            return [
                {
                    "session_id": row.session_id,
                    "issued_at": _utc(row.issued_at),
                    "expires_at": _utc(row.expires_at),
                    "active_workspace_id": row.active_workspace_id,
                    "revoked": not row.active or row.revoked_at is not None,
                    "expired": _utc(row.expires_at) <= datetime.now(UTC),
                    "last_activity_at": _utc(row.last_activity_at)
                    if row.last_activity_at
                    else None,
                }
                for row in rows.all()
            ]

    async def revoke_actor_session(
        self,
        actor_id: str,
        session_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            actor = await session.get(ActorRecord, actor_id)
            record = await session.get(AuthSessionRecord, session_id)
            if (
                actor is None
                or actor.tenant_id != tenant_id
                or actor.organization_id != organization_id
                or record is None
                or record.actor_id != actor_id
            ):
                raise ValueError("session is outside the authority boundary")

    async def import_employee(self, employee: EmployeeRecord) -> EmployeeRecord:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            tenant = await session.get(TenantRecord, employee.tenant_id)
            org = await session.get(OrganizationRecord, employee.organization_id)
            workspace = await session.get(WorkspaceRecord, employee.workspace_id)
            if (
                tenant is None
                or org is None
                or workspace is None
                or not tenant.active
                or not org.active
                or not workspace.active
            ):
                raise ValueError("tenant, organization, or workspace does not exist or is inactive")
            if (
                workspace.tenant_id != employee.tenant_id
                or workspace.organization_id != employee.organization_id
            ):
                raise ValueError("workspace belongs to a different authority boundary")

            existing_by_id = await session.get(EmployeeRecord, employee.employee_id)
            if existing_by_id is not None:
                if (
                    existing_by_id.tenant_id != employee.tenant_id
                    or existing_by_id.organization_id != employee.organization_id
                ):
                    raise ValueError("employee exists in a different tenant or organization")
                conflict = await session.scalar(
                    select(EmployeeRecord).where(
                        EmployeeRecord.tenant_id == employee.tenant_id,
                        EmployeeRecord.organization_id == employee.organization_id,
                        EmployeeRecord.employee_number == employee.employee_number,
                        EmployeeRecord.employee_id != employee.employee_id,
                    )
                )
                if conflict is not None:
                    raise ValueError("employee_number already exists for another employee")
                if employee.email:
                    email_conflict = await session.scalar(
                        select(EmployeeRecord).where(
                            EmployeeRecord.tenant_id == employee.tenant_id,
                            EmployeeRecord.organization_id == employee.organization_id,
                            EmployeeRecord.email == employee.email,
                            EmployeeRecord.employee_id != employee.employee_id,
                        )
                    )
                    if email_conflict is not None:
                        raise ValueError("email already exists for another employee")
                existing_by_id.employee_number = employee.employee_number
                existing_by_id.full_name = employee.full_name
                existing_by_id.email = employee.email
                existing_by_id.workspace_id = employee.workspace_id
                existing_by_id.department_code = employee.department_code
                existing_by_id.position_title = employee.position_title
                existing_by_id.employment_status = employee.employment_status
                existing_by_id.join_date = employee.join_date
                existing_by_id.end_date = employee.end_date
                existing_by_id.updated_at = now
                await session.flush()
                return existing_by_id

            num_conflict = await session.scalar(
                select(EmployeeRecord).where(
                    EmployeeRecord.tenant_id == employee.tenant_id,
                    EmployeeRecord.organization_id == employee.organization_id,
                    EmployeeRecord.employee_number == employee.employee_number,
                )
            )
            if num_conflict is not None:
                raise ValueError("employee_number already exists")
            if employee.email:
                email_conflict = await session.scalar(
                    select(EmployeeRecord).where(
                        EmployeeRecord.tenant_id == employee.tenant_id,
                        EmployeeRecord.organization_id == employee.organization_id,
                        EmployeeRecord.email == employee.email,
                    )
                )
                if email_conflict is not None:
                    raise ValueError("email already exists for another employee")
            employee.created_at = now
            employee.updated_at = now
            session.add(employee)
            await session.flush()
            return employee

    async def resend_activation_challenge(
        self,
        actor_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        new_token_hash: str,
        new_expires_at: datetime,
    ) -> tuple[AccountState, str | None, str | None]:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            actor = await session.get(ActorRecord, actor_id)
            account = await session.scalar(
                select(AuthAccountRecord)
                .where(
                    AuthAccountRecord.actor_id == actor_id,
                    AuthAccountRecord.tenant_id == tenant_id,
                    AuthAccountRecord.organization_id == organization_id,
                )
                .with_for_update()
            )
            if actor is None or account is None or not actor.active or not account.active:
                raise ValueError("account is not found or inactive")
            if account.activation_state == "ACTIVATED":
                raise ValueError("account is already activated")

            challenges = (
                await session.scalars(
                    select(ActivationChallengeRecord).where(
                        ActivationChallengeRecord.account_id == account.account_id,
                        ActivationChallengeRecord.consumed_at.is_(None),
                    )
                )
            ).all()
            for challenge in challenges:
                challenge.consumed_at = now

            challenge_id = f"activation_{actor_id}_{uuid.uuid4().hex[:8]}"
            new_challenge = ActivationChallengeRecord(
                challenge_id=challenge_id,
                account_id=account.account_id,
                token_hash=new_token_hash,
                created_at=now,
                expires_at=new_expires_at,
                consumed_at=None,
            )
            session.add(new_challenge)
            account.updated_at = now
            await session.flush()

            employee = await session.scalar(
                select(EmployeeRecord).where(EmployeeRecord.actor_id == actor_id)
            )
            workspace = None
            if account.primary_workspace_id:
                workspace = await session.get(WorkspaceRecord, account.primary_workspace_id)

            return (
                _account_state(account),
                employee.full_name if employee else account.display_name,
                workspace.name if workspace else None,
            )

    async def create_password_reset_challenge(
        self,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> tuple[AccountState, str] | None:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            account = await session.scalar(
                select(AuthAccountRecord)
                .where(
                    AuthAccountRecord.email == email.strip().lower(),
                    AuthAccountRecord.active.is_(True),
                    AuthAccountRecord.administrative_state == "ENABLED",
                    AuthAccountRecord.activation_state == "ACTIVATED",
                )
                .with_for_update()
            )
            if account is None:
                return None
            actor = await session.get(ActorRecord, account.actor_id)
            if actor is None or not actor.active:
                return None

            challenges = (
                await session.scalars(
                    select(PasswordResetChallengeRecord).where(
                        PasswordResetChallengeRecord.account_id == account.account_id,
                        PasswordResetChallengeRecord.consumed_at.is_(None),
                    )
                )
            ).all()
            for ch in challenges:
                ch.consumed_at = now

            session.add(
                PasswordResetChallengeRecord(
                    challenge_id=f"reset_{account.actor_id}_{uuid.uuid4().hex[:8]}",
                    account_id=account.account_id,
                    token_hash=token_hash,
                    created_at=now,
                    expires_at=expires_at,
                    consumed_at=None,
                )
            )
            await session.flush()
            return _account_state(account), account.display_name

    async def confirm_password_reset(
        self,
        token_hash: str,
        new_password_hash: str,
        reset_at: datetime,
    ) -> AccountState | None:
        async with self._session_factory() as session, session.begin():
            challenge = await session.scalar(
                select(PasswordResetChallengeRecord)
                .where(
                    PasswordResetChallengeRecord.token_hash == token_hash,
                    PasswordResetChallengeRecord.consumed_at.is_(None),
                    PasswordResetChallengeRecord.expires_at > reset_at,
                )
                .with_for_update()
            )
            if challenge is None:
                return None
            account = await session.get(AuthAccountRecord, challenge.account_id)
            if account is None or not account.active or account.administrative_state != "ENABLED":
                return None

            account.password_hash = new_password_hash
            account.updated_at = reset_at
            challenge.consumed_at = reset_at

            sessions = (
                await session.scalars(
                    select(AuthSessionRecord).where(
                        AuthSessionRecord.account_id == account.account_id,
                        AuthSessionRecord.active.is_(True),
                    )
                )
            ).all()
            for s in sessions:
                s.active = False
                s.revoked_at = reset_at

            await session.flush()
            return _account_state(account)

    async def revoke_all_sessions(
        self,
        actor_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            actor = await session.get(ActorRecord, actor_id)
            if (
                actor is None
                or actor.tenant_id != tenant_id
                or actor.organization_id != organization_id
            ):
                raise ValueError("actor is outside the authority boundary")
            sessions = (
                await session.scalars(
                    select(AuthSessionRecord).where(
                        AuthSessionRecord.actor_id == actor_id,
                        AuthSessionRecord.active.is_(True),
                    )
                )
            ).all()
            for s in sessions:
                s.active = False
                s.revoked_at = revoked_at

    @staticmethod
    async def _membership_boundary(
        session: AsyncSession, command: MembershipMutation
    ) -> tuple[ActorRecord, WorkspaceRecord]:
        actor = await session.get(ActorRecord, command.actor_id)
        workspace = await session.get(WorkspaceRecord, command.workspace_id)
        if (
            actor is None
            or workspace is None
            or actor.tenant_id != command.tenant_id
            or actor.organization_id != command.organization_id
            or workspace.tenant_id != command.tenant_id
            or workspace.organization_id != command.organization_id
            or not workspace.active
        ):
            raise ValueError("membership target is outside the active authority boundary")
        return actor, workspace

    @staticmethod
    async def _account_record(session: AsyncSession, email: str) -> AuthAccountRecord | None:
        result = await session.execute(
            select(AuthAccountRecord).where(AuthAccountRecord.email == email)
        )
        return result.scalar_one_or_none()


def _account_state(record: AuthAccountRecord) -> AccountState:
    return AccountState(
        record.account_id,
        record.email,
        record.password_hash,
        record.actor_id,
        record.tenant_id,
        record.organization_id,
        record.display_name,
        record.active,
        record.administrative_state,
        record.activation_state,
        record.primary_workspace_id,
    )


def _access_state(membership: WorkspaceMembershipRecord, workspace: WorkspaceRecord) -> AccessState:
    now = datetime.now(UTC)
    return AccessState(
        workspace.workspace_id,
        workspace.workspace_key,
        workspace.name,
        workspace.workspace_type,
        workspace.organization_id,
        workspace.organizational_unit_id,
        workspace.division_code,
        tuple(membership.roles),
        tuple(membership.permission_refs),
        tuple(membership.scope_refs),
        membership.data_scope,
        membership.active
        and membership.revoked_at is None
        and membership.effective_at <= now
        and (membership.expires_at is None or membership.expires_at > now)
        and workspace.active,
    )


def _workspace_state(record: WorkspaceRecord) -> WorkspaceState:
    return WorkspaceState(
        record.workspace_id,
        record.workspace_key,
        record.name,
        record.workspace_type,
        record.tenant_id,
        record.organization_id,
        record.organizational_unit_id,
        record.division_code,
        record.active,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
