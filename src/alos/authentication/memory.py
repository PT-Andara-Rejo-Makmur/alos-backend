"""Explicit test-only authentication repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from alos.authentication.repository import (
    AccessState,
    AccountState,
    MembershipMutation,
    ProvisionAccount,
    SessionState,
    WorkspaceState,
)
from alos.persistence.models import EmployeeRecord


class InMemoryAuthRepository:
    """Unit/integration test double; never selected for non-test application environments."""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountState] = {}
        self._access: dict[str, list[AccessState]] = {}
        self._workspaces: dict[str, AccessState] = {}
        self._workspace_tenants: dict[str, str] = {}
        self._sessions: dict[str, tuple[str, SessionState]] = {}
        self._employees: dict[str, dict[str, str | None]] = {}
        self._activation_challenges: dict[str, tuple[str, datetime]] = {}
        self._password_reset_challenges: dict[str, tuple[str, datetime]] = {}
        self._membership_windows: dict[tuple[str, str], tuple[datetime, datetime | None]] = {}
        self._next_account_id = 1

    def add_test_employee(
        self,
        employee_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        full_name: str,
        employment_status: str = "ACTIVE",
    ) -> None:
        self._employees[employee_id] = {
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "full_name": full_name,
            "employee_number": employee_id,
            "email": None,
            "department_code": None,
            "position_title": None,
            "employment_status": employment_status,
            "actor_id": None,
        }

    async def provision(
        self,
        command: ProvisionAccount,
        *,
        bootstrap: bool,
        initial_authority: bool = False,
    ) -> AccountState:
        if initial_authority and any(
            "IT_ADMIN" in access.role_refs or "identity.accounts.manage" in access.permission_refs
            for accesses in self._access.values()
            for access in accesses
            if access.active
        ):
            raise ValueError("initial identity authority already exists")
        if command.email in self._accounts:
            raise ValueError("account already exists")
        employee = None
        if not bootstrap:
            employee = self._employees.get(command.employee_id or "")
            if (
                employee is None
                or employee["tenant_id"] != command.tenant_id
                or employee["organization_id"] != command.organization_id
                or employee["employment_status"] != "ACTIVE"
                or employee["actor_id"] is not None
            ):
                raise ValueError("employee is not eligible for account provisioning")
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
            str(employee["full_name"]) if employee is not None else command.display_name,
            True,
            "ENABLED",
            "ACTIVATED" if bootstrap else "PENDING",
            (
                command.workspace_id
                if (command.effective_at or datetime.now(UTC)) <= datetime.now(UTC)
                and (command.expires_at is None or command.expires_at > datetime.now(UTC))
                else None
            ),
        )
        self._next_account_id += 1
        self._accounts[command.email] = account
        workspace_state = self._workspaces.get(command.workspace_id)
        initial_access = AccessState(
            command.workspace_id,
            workspace_state.workspace_key if workspace_state else command.workspace_key,
            workspace_state.workspace_name if workspace_state else command.workspace_name,
            workspace_state.workspace_type if workspace_state else command.workspace_type,
            command.organization_id,
            (
                workspace_state.organizational_unit_id
                if workspace_state
                else command.organizational_unit_id
            ),
            workspace_state.division_code if workspace_state else command.division_code,
            command.role_refs,
            command.permission_refs,
            command.scope_refs,
            command.data_scope,
            True,
        )
        self._access[command.actor_id] = [initial_access]
        self._membership_windows[(command.actor_id, command.workspace_id)] = (
            command.effective_at or datetime.now(UTC),
            command.expires_at,
        )
        self._workspaces[command.workspace_id] = initial_access
        self._workspace_tenants[command.workspace_id] = command.tenant_id
        if employee is not None:
            employee["actor_id"] = command.actor_id
            if command.activation_token_hash and command.activation_expires_at:
                self._activation_challenges[command.activation_token_hash] = (
                    command.actor_id,
                    command.activation_expires_at,
                )
        return account

    async def account_by_email(self, email: str) -> AccountState | None:
        return self._accounts.get(email)

    async def provisioning_candidates(
        self, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, str | None]]:
        return [
            {
                "employee_id": employee_id,
                "employee_number": employee["employee_number"],
                "full_name": employee["full_name"],
                "email": employee["email"],
                "department_code": employee["department_code"],
                "position_title": employee["position_title"],
                "employment_status": "ACTIVE",
                "linkage_state": "AVAILABLE",
            }
            for employee_id, employee in self._employees.items()
            if employee["tenant_id"] == tenant_id
            and employee["organization_id"] == organization_id
            and employee["employment_status"] == "ACTIVE"
            and employee["actor_id"] is None
        ]

    async def active_access(self, actor_id: str) -> list[AccessState]:
        account = next(
            (candidate for candidate in self._accounts.values() if candidate.actor_id == actor_id),
            None,
        )
        if account is None or not account.active:
            return []
        now = datetime.now(UTC)
        return [
            access
            for access in self._access.get(actor_id, [])
            if self._membership_is_active(actor_id, access, now)
        ]

    async def all_access(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[AccessState]:
        account = self._bounded_account(actor_id, tenant_id, organization_id)
        del account
        return list(self._access.get(actor_id, []))

    async def accounts(self, *, tenant_id: str, organization_id: str) -> list[AccountState]:
        accounts = [
            account
            for account in self._accounts.values()
            if account.tenant_id == tenant_id and account.organization_id == organization_id
        ]
        projected = []
        for account in accounts:
            expires_at = max(
                (
                    expires
                    for _token_hash, (actor_id, expires) in self._activation_challenges.items()
                    if actor_id == account.actor_id
                ),
                default=None,
            )
            has_challenge = any(
                aid == account.actor_id for _th, (aid, _) in self._activation_challenges.items()
            )
            delivered = (
                True
                if account.activation_state == "ACTIVATED"
                else (True if has_challenge else account.email_delivered)
            )
            new_state = (
                "EXPIRED"
                if account.activation_state == "PENDING"
                and expires_at is not None
                and expires_at <= datetime.now(UTC)
                else account.activation_state
            )
            projected.append(
                replace(
                    account,
                    activation_state=new_state,
                    email_delivered=delivered,
                )
            )
        return sorted(projected, key=lambda account: account.email)

    async def workspace(self, workspace_id: str) -> WorkspaceState | None:
        access = self._workspaces.get(workspace_id)
        if access is None:
            return None
        return WorkspaceState(
            access.workspace_id,
            access.workspace_key,
            access.workspace_name,
            access.workspace_type,
            self._workspace_tenants[workspace_id],
            access.organization_id,
            access.organizational_unit_id,
            access.division_code,
            access.active,
        )

    async def list_organization_workspaces(
        self, *, tenant_id: str, organization_id: str
    ) -> list[WorkspaceState]:
        result = []
        for workspace_id in self._workspaces:
            workspace = await self.workspace(workspace_id)
            if (
                workspace is not None
                and workspace.tenant_id == tenant_id
                and workspace.organization_id == organization_id
                and workspace.active
            ):
                result.append(workspace)
        return sorted(result, key=lambda workspace: workspace.workspace_key.lower())

    async def assign_membership(self, command: MembershipMutation) -> AccessState:
        self._bounded_account(command.actor_id, command.tenant_id, command.organization_id)
        workspace = self._bounded_workspace(command)
        if any(
            item.workspace_id == command.workspace_id for item in self._access[command.actor_id]
        ):
            raise ValueError("workspace membership already exists")
        access = self._mutated_access(command, workspace, active=True)
        self._access[command.actor_id].append(access)
        self._membership_windows[(command.actor_id, command.workspace_id)] = (
            command.effective_at or datetime.now(UTC),
            command.expires_at,
        )
        return access

    async def update_membership(self, command: MembershipMutation) -> AccessState:
        self._bounded_account(command.actor_id, command.tenant_id, command.organization_id)
        workspace = self._bounded_workspace(command)
        items = self._access.get(command.actor_id, [])
        for index, existing in enumerate(items):
            if existing.workspace_id == command.workspace_id and existing.active:
                updated = self._mutated_access(command, workspace, active=True)
                items[index] = updated
                self._membership_windows[(command.actor_id, command.workspace_id)] = (
                    command.effective_at or datetime.now(UTC),
                    command.expires_at,
                )
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
            "ENABLED" if active else "SUSPENDED",
            current.activation_state,
            current.primary_workspace_id,
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

    async def admin_sessions(
        self, actor_id: str, *, tenant_id: str, organization_id: str
    ) -> list[dict[str, object]]:
        account = next(
            (item for item in self._accounts.values() if item.actor_id == actor_id), None
        )
        if (
            account is None
            or account.tenant_id != tenant_id
            or account.organization_id != organization_id
        ):
            raise ValueError("actor is outside the authority boundary")
        return [
            {
                "session_id": state.session_id,
                "issued_at": state.issued_at,
                "expires_at": state.expires_at,
                "active_workspace_id": state.active_workspace_id,
                "revoked": False,
                "expired": state.expires_at <= datetime.now(UTC),
                "last_activity_at": state.issued_at,
            }
            for _, state in self._sessions.values()
            if state.account.actor_id == actor_id
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
        current = await self.admin_sessions(
            actor_id, tenant_id=tenant_id, organization_id=organization_id
        )
        if not any(row["session_id"] == session_id for row in current):
            raise ValueError("session is outside the authority boundary")
        await self.revoke_session(session_id, revoked_at)

    async def activate_account(
        self, token_hash: str, password_hash: str, activated_at: datetime
    ) -> AccountState | None:
        challenge = self._activation_challenges.pop(token_hash, None)
        if challenge is None or challenge[1] <= activated_at:
            return None
        account = next(
            (item for item in self._accounts.values() if item.actor_id == challenge[0]),
            None,
        )
        if account is None or account.activation_state != "PENDING":
            return None
        activated = AccountState(
            account.account_id,
            account.email,
            password_hash,
            account.actor_id,
            account.tenant_id,
            account.organization_id,
            account.display_name,
            account.active,
            account.administrative_state,
            "ACTIVATED",
            account.primary_workspace_id,
        )
        self._accounts[account.email] = activated
        return activated

    async def import_employee(self, employee: EmployeeRecord) -> EmployeeRecord:
        workspace = self._workspaces.get(employee.workspace_id)
        if (
            workspace is None
            or self._workspace_tenants.get(employee.workspace_id) != employee.tenant_id
            or workspace.organization_id != employee.organization_id
            or not workspace.active
        ):
            raise ValueError("workspace belongs to a different authority boundary")

        for emp_id, emp in self._employees.items():
            if (
                emp_id != employee.employee_id
                and emp.get("tenant_id") == employee.tenant_id
                and emp.get("organization_id") == employee.organization_id
            ):
                if emp.get("employee_number") == employee.employee_number:
                    raise ValueError("employee_number already exists")
                if employee.email and emp.get("email") == employee.email:
                    raise ValueError("employee email already exists")

        self._employees[employee.employee_id] = {
            "employee_id": employee.employee_id,
            "tenant_id": employee.tenant_id,
            "organization_id": employee.organization_id,
            "workspace_id": employee.workspace_id,
            "employee_number": employee.employee_number,
            "full_name": employee.full_name,
            "email": employee.email,
            "department_code": employee.department_code,
            "position_title": employee.position_title,
            "employment_status": employee.employment_status,
            "actor_id": None,
        }
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
        account = self._bounded_account(actor_id, tenant_id, organization_id)
        if account.activation_state == "ACTIVATED":
            raise ValueError("account is already activated")

        self._activation_challenges = {
            k: v for k, v in self._activation_challenges.items() if v[0] != actor_id
        }
        self._activation_challenges[new_token_hash] = (actor_id, new_expires_at)

        emp = next((v for v in self._employees.values() if v.get("actor_id") == actor_id), None)
        workspace = self._workspaces.get(account.primary_workspace_id or "")
        return (
            account,
            emp["full_name"] if emp else account.display_name,
            workspace.workspace_name if workspace else None,
        )

    async def create_password_reset_challenge(
        self,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> tuple[AccountState, str] | None:
        account = self._accounts.get(email.strip().lower())
        if (
            account is None
            or not account.active
            or account.administrative_state != "ENABLED"
            or account.activation_state != "ACTIVATED"
        ):
            return None
        self._password_reset_challenges = {
            k: v for k, v in self._password_reset_challenges.items() if v[0] != account.actor_id
        }
        self._password_reset_challenges[token_hash] = (account.actor_id, expires_at)
        return account, account.display_name

    async def confirm_password_reset(
        self,
        token_hash: str,
        new_password_hash: str,
        reset_at: datetime,
    ) -> AccountState | None:
        challenge = self._password_reset_challenges.pop(token_hash, None)
        if challenge is None or challenge[1] <= reset_at:
            return None
        actor_id = challenge[0]
        account = next(
            (item for item in self._accounts.values() if item.actor_id == actor_id),
            None,
        )
        if account is None or not account.active or account.administrative_state != "ENABLED":
            return None
        updated = AccountState(
            account.account_id,
            account.email,
            new_password_hash,
            account.actor_id,
            account.tenant_id,
            account.organization_id,
            account.display_name,
            account.active,
            account.administrative_state,
            account.activation_state,
            account.primary_workspace_id,
        )
        self._accounts[account.email] = updated
        self._sessions = {
            k: v for k, v in self._sessions.items() if v[1].account.actor_id != actor_id
        }
        return updated

    async def revoke_all_sessions(
        self,
        actor_id: str,
        *,
        tenant_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None:
        del revoked_at
        self._bounded_account(actor_id, tenant_id, organization_id)
        self._sessions = {
            k: v for k, v in self._sessions.items() if v[1].account.actor_id != actor_id
        }

    def _bounded_account(self, actor_id: str, tenant_id: str, organization_id: str) -> AccountState:
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

    def _membership_is_active(self, actor_id: str, access: AccessState, now: datetime) -> bool:
        effective_at, expires_at = self._membership_windows.get(
            (actor_id, access.workspace_id), (now, None)
        )
        return access.active and effective_at <= now and (expires_at is None or expires_at > now)

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
