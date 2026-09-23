"""Authentication persistence ports and production PostgreSQL implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.persistence.models import (
    ActorRecord,
    AuthAccountRecord,
    AuthSessionRecord,
    OrganizationRecord,
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


class AuthRepository(Protocol):
    async def provision(self, command: ProvisionAccount, *, bootstrap: bool) -> AccountState: ...

    async def account_by_email(self, email: str) -> AccountState | None: ...

    async def active_access(self, actor_id: str) -> list[AccessState]: ...

    async def all_access(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[AccessState]: ...

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
        active_workspace_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> SessionState: ...

    async def session_by_token_hash(self, token_hash: str) -> SessionState | None: ...

    async def set_active_workspace(self, session_id: str, workspace_id: str) -> None: ...

    async def revoke_session(self, session_id: str, revoked_at: datetime) -> None: ...


class SqlAuthRepository:
    """PostgreSQL-backed authority for accounts, sessions, and workspace memberships."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def provision(self, command: ProvisionAccount, *, bootstrap: bool) -> AccountState:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            if await self._account_record(session, command.email) is not None:
                raise ValueError("account already exists")
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
                display_name=command.display_name,
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
                display_name=command.display_name,
                created_at=now,
                updated_at=now,
                active=True,
            )
            session.add(account)
            await session.flush()
            return _account_state(account)

    async def account_by_email(self, email: str) -> AccountState | None:
        async with self._session_factory() as session:
            record = await self._account_record(session, email)
            return _account_state(record) if record is not None else None

    async def active_access(self, actor_id: str) -> list[AccessState]:
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
        active_workspace_id: str,
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
                revoked_at=None,
                active=True,
            )
            session.add(record)
        return SessionState(session_id, account, active_workspace_id, issued_at, expires_at)

    async def session_by_token_hash(self, token_hash: str) -> SessionState | None:
        async with self._session_factory() as session:
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
            expires_at = _utc(current.expires_at)
            if expires_at <= datetime.now(UTC):
                return None
            return SessionState(
                current.session_id,
                _account_state(account),
                current.active_workspace_id,
                _utc(current.issued_at),
                expires_at,
            )

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
    )


def _access_state(membership: WorkspaceMembershipRecord, workspace: WorkspaceRecord) -> AccessState:
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
        membership.active and workspace.active,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
