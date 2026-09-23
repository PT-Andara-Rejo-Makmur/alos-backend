# Canonical Identity and Workspace Access

ALOS Backend is the only authority for authentication, actor identity, organization boundary,
workspace membership, roles, permissions, scopes, and active workspace. Web projects these facts;
GENESIS receives only the Backend-issued execution context it needs.

## Persistent model

- `auth_accounts` owns credential state and links an account to one canonical actor.
- `identity_actors` owns tenant, organization, display name, and active state.
- `identity_workspaces` owns stable workspace key, type, organizational unit, division, and active state.
- `identity_memberships` owns per-workspace role, permission, scope, data scope, and revocation state.
- `auth_sessions` stores only a SHA-256 digest of a random opaque token, an expiry, revocation state,
  and the selected active workspace.

Migration `0008_canonical_identity_access` backfills the new workspace/session fields before making
canonical fields required. Its downgrade is intentionally disabled because reverting would discard
authority and revocation semantics.

## Public session flow

1. `POST /api/v1/auth/login` verifies the password and creates a persisted, expiring session.
2. `GET /api/v1/auth/whoami` resolves the account, actor, active memberships, and active workspace
   again; it does not trust identity facts supplied by the caller.
3. `GET /api/v1/workspaces` returns only active memberships for the authenticated actor.
4. `PUT /api/v1/auth/active-workspace` accepts a workspace id only when an active membership exists.
5. `POST /api/v1/auth/logout` revokes the persisted session.

The bearer value is an opaque session token, not a JWT. Production uses the SQL repository.
The in-memory repository is selected only for `APP_ENV=test`.

## Provisioning and vocabulary

Production account creation is `POST /api/v1/identity/accounts` and requires the
`identity.accounts.manage` permission. `/api/v1/auth/register` is excluded from OpenAPI and returns
not-found unless explicitly enabled in test/development.

Administrative lifecycle operations are actor-scoped and organization-bounded:

- list access requires `identity.memberships.read`;
- assign, replace, or revoke a membership requires `identity.memberships.manage`;
- activate or suspend an account requires `identity.accounts.manage`.

They use the shared authorization enforcer instead of role-name checks. Each successful material
change records the authenticated administrator actor and correlation id in the append-only audit
sink. Suspending an account also revokes its current sessions; activating it never restores them.

Canonical roles are `EXECUTIVE`, `WORKSPACE_LEAD`, `WORKSPACE_MEMBER`, `BUSINESS_REVIEWER`,
`IT_ADMIN`, `AI_ADMIN`, `TECHNICAL_REVIEWER`, and `QA_ASSURANCE`. Legacy role aliases are accepted
only at the gated bootstrap boundary and are normalized before persistence.

## Fail-closed invariants

- inactive account, actor, workspace, or membership grants no access;
- expired or revoked sessions are rejected;
- selecting a workspace cannot create or expand membership;
- public requests cannot override tenant, organization, role, permission, scope, or data scope;
- downstream runtime authorization is derived from the active Backend membership.
