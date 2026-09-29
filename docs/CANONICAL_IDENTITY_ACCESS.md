# Identity and Access

ALOS Backend is the authority for account lifecycle, workspace membership, and session state. The
active authorization roles are `EXECUTIVE`, `DIVISION_LEAD`, `DIVISION_MEMBER`, and `IT_ADMIN`.
Each workspace membership carries exactly one role. Effective and optional expiration times are
checked with revocation, workspace state, and account state before access is projected.

## Employee provisioning

`hr.employees` remains the employee source. Identity uses `employee_id` and `actor_id` to link an
existing eligible employee to an account; it does not modify employment, department, position, or
employment dates. `GET /api/v1/identity/provisioning-candidates` returns the minimum employee fields
after Backend filters to the caller's tenant and organization, active employment, current employment
dates, and unlinked records.

`POST /api/v1/identity/accounts` accepts employee, account email, initial workspace, one role,
effective and optional expiration times, and a note. Backend derives tenant and organization from
the authenticated administrator, checks the workspace and role policy, then atomically creates the
actor, account, initial membership, employee link, and activation challenge. The request cannot
select permissions, scopes, data scope, or a password.

New accounts begin `ENABLED` and `PENDING`. Login remains denied until the employee activates with
a one-time expiring token and chooses a password. The database stores a challenge hash only. Challenge
creation does not imply delivery; this backend does not claim that an email or message was sent.
The activation sink is available only when `APP_ENV=test`.

## Membership and account lifecycle

The account's primary workspace is a Backend-owned reference to an active membership. It does not
grant access or select a session's active workspace. Revoking the primary membership clears the
reference and clears any session selection for that workspace. Membership revocation is soft and
preserves history.

Account administration exposes separate administrative and activation states. Suspending an account
blocks login and revokes its sessions without changing HR status. Reactivation enables the account
but does not restore revoked memberships or sessions.

Session administration returns only session ID, timestamps, active workspace, revocation state, and
last activity. Token values and token hashes are never projected. Identity history is read from the
append-only audit store and omits credential material.

## Authority and errors

All identity administration is bounded to the caller's tenant and organization and requires the
Backend permission policy plus an active `IT_ADMIN` membership where specified by the route. The
`IT_ADMIN` role alone grants no Finance, HR, Legal, or other business-domain access. `EXECUTIVE` alone
grants no identity administration permission. Client authority fields are rejected by strict request
models.

Duplicate account or membership state returns a conflict. Missing resources return not found,
authorization failures return forbidden, and invalid requests return validation errors. Failed
provisioning rolls back the employee link and all created identity records together.

## Storage migration

Migration `0023_identity_access` is append-only. It converts only equivalent persisted workspace
roles and policy grants, preserves permission and scope metadata, and adds membership dates,
account lifecycle fields, primary workspace, session activity, and activation challenges. It fails
closed when a membership has multiple roles or an active non-equivalent legacy role or grant remains.
Historical migrations remain unchanged.
