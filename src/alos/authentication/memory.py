"""Explicit test-only authentication repository."""

from __future__ import annotations

from datetime import datetime

from alos.authentication.repository import (
    AccessState,
    AccountState,
    MembershipMutation,
    ProvisionAccount,
    SessionState,
)


class InMemoryAuthRepository:
    """Unit/integration test double; never selected for non-test application environments."""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountState] = {}
        self._access: dict[str, list[AccessState]] = {}
        self._workspaces: dict[str, AccessState] = {}
        self._workspace_tenants: dict[str, str] = {}
        self._sessions: dict[str, tuple[str, SessionState]] = {}
        self._next_account_id = 1

    async def provision(
        self,
        command: ProvisionAccount,
        *,
        bootstrap: bool,
        initial_authority: bool = False,
    ) -> AccountState:
        if initial_authority and any(
            "IT_ADMIN" in access.role_refs
            or "identity.accounts.manage" in access.permission_refs
            for accesses in self._access.values()
            for access in accesses
            if access.active
        ):
            raise ValueError("initial identity authority already exists")
        if command.email in self._accounts:
            raise ValueError("account already exists")
        if not bootstrap:
            workspace = self._workspaces.get(command.workspace_id)
            if (
                workspace is None
                or self._workspace_tenants.get(command.workspace_id) != command.tenant_id
                or workspace.organization_id != command.organization_id
                or not workspace.active
            ):
                raise ValueError("provisioning target is outside an active authority boundary")
        account = AccountState(
            self._next_account_id,
            command.email,
            command.password_hash,
            command.actor_id,
            command.tenant_id,
            command.organization_id,
            command.display_name,
            True,
        )
        self._next_account_id += 1
        self._accounts[command.email] = account
        initial_access = AccessState(
                command.workspace_id,
                command.workspace_key,
                command.workspace_name,
                command.workspace_type,
                command.organization_id,
                command.organizational_unit_id,
                command.division_code,
                command.role_refs,
                command.permission_refs,
                command.scope_refs,
                command.data_scope,
                True,
            )
        self._access[command.actor_id] = [initial_access]
        self._workspaces[command.workspace_id] = initial_access
        self._workspace_tenants[command.workspace_id] = command.tenant_id
        return account

    async def account_by_email(self, email: str) -> AccountState | None:
        return self._accounts.get(email)

    async def active_access(self, actor_id: str) -> list[AccessState]:
        account = next(
            (candidate for candidate in self._accounts.values() if candidate.actor_id == actor_id),
            None,
        )
        if account is None or not account.active:
            return []
        return [access for access in self._access.get(actor_id, []) if access.active]

    async def all_access(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[AccessState]:
        account = self._bounded_account(actor_id, tenant_id, organization_id)
        del account
        return list(self._access.get(actor_id, []))

    async def assign_membership(self, command: MembershipMutation) -> AccessState:
        self._bounded_account(command.actor_id, command.tenant_id, command.organization_id)
        workspace = self._bounded_workspace(command)
        if any(
            item.workspace_id == command.workspace_id
            for item in self._access[command.actor_id]
        ):
            raise ValueError("workspace membership already exists")
        access = self._mutated_access(command, workspace, active=True)
        self._access[command.actor_id].append(access)
        return access

    async def update_membership(self, command: MembershipMutation) -> AccessState:
        self._bounded_account(command.actor_id, command.tenant_id, command.organization_id)
        workspace = self._bounded_workspace(command)
        items = self._access.get(command.actor_id, [])
        for index, existing in enumerate(items):
            if existing.workspace_id == command.workspace_id and existing.active:
                updated = self._mutated_access(command, workspace, active=True)
                items[index] = updated
                return updated
        raise ValueError("active workspace membership does not exist")

    async def revoke_membership(
        self,
        actor_id: str,
        workspace_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None:
        del revoked_at
        self._bounded_account(actor_id, tenant_id, organization_id)
        items = self._access.get(actor_id, [])
        for index, existing in enumerate(items):
            if existing.workspace_id == workspace_id and existing.active:
                items[index] = AccessState(
                    existing.workspace_id,
                    existing.workspace_key,
                    existing.workspace_name,
                    existing.workspace_type,
                    existing.organization_id,
                    existing.organizational_unit_id,
                    existing.division_code,
                    existing.role_refs,
                    existing.permission_refs,
                    existing.scope_refs,
                    existing.data_scope,
                    False,
                )
                return
        raise ValueError("active workspace membership does not exist")

    async def set_account_active(
        self,
        actor_id: str,
        active: bool,
        *,
        tenant_id: str,
        organization_id: str,
        changed_at: datetime,
    ) -> AccountState:
        del changed_at
        current = self._bounded_account(actor_id, tenant_id, organization_id)
        updated = AccountState(
            current.account_id,
            current.email,
            current.password_hash,
            current.actor_id,
            current.tenant_id,
            current.organization_id,
            current.display_name,
            active,
        )
        self._accounts[current.email] = updated
        if not active:
            self._sessions = {
                token: value
                for token, value in self._sessions.items()
                if value[1].account.actor_id != actor_id
            }
        return updated

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
        state = SessionState(session_id, account, active_workspace_id, issued_at, expires_at)
        self._sessions[token_hash] = (session_id, state)
        return state

    async def session_by_token_hash(self, token_hash: str) -> SessionState | None:
        value = self._sessions.get(token_hash)
        if value is None or value[1].expires_at <= datetime.now(value[1].expires_at.tzinfo):
            return None
        account = self._accounts.get(value[1].account.email)
        if account is None or not account.active:
            return None
        return value[1]

    async def set_active_workspace(self, session_id: str, workspace_id: str) -> None:
        for token_hash, (current_id, state) in self._sessions.items():
            if current_id == session_id:
                self._sessions[token_hash] = (
                    current_id,
                    SessionState(
                        state.session_id,
                        state.account,
                        workspace_id,
                        state.issued_at,
                        state.expires_at,
                    ),
                )
                return
        raise ValueError("session is not active")

    async def revoke_session(self, session_id: str, revoked_at: datetime) -> None:
        del revoked_at
        for token_hash, (current_id, _) in list(self._sessions.items()):
            if current_id == session_id:
                del self._sessions[token_hash]

    def _bounded_account(
        self, actor_id: str, tenant_id: str, organization_id: str
    ) -> AccountState:
        account = next(
            (candidate for candidate in self._accounts.values() if candidate.actor_id == actor_id),
            None,
        )
        if (
            account is None
            or account.tenant_id != tenant_id
            or account.organization_id != organization_id
        ):
            raise ValueError("account is outside the authority boundary")
        return account

    def _bounded_workspace(self, command: MembershipMutation) -> AccessState:
        workspace = self._workspaces.get(command.workspace_id)
        if (
            workspace is None
            or self._workspace_tenants.get(command.workspace_id) != command.tenant_id
            or workspace.organization_id != command.organization_id
            or not workspace.active
        ):
            raise ValueError("membership target is outside the active authority boundary")
        return workspace

    @staticmethod
    def _mutated_access(
        command: MembershipMutation, workspace: AccessState, *, active: bool
    ) -> AccessState:
        return AccessState(
            workspace.workspace_id,
            workspace.workspace_key,
            workspace.workspace_name,
            workspace.workspace_type,
            workspace.organization_id,
            workspace.organizational_unit_id,
            workspace.division_code,
            command.role_refs,
            command.permission_refs,
            command.scope_refs,
            command.data_scope,
            active,
        )
