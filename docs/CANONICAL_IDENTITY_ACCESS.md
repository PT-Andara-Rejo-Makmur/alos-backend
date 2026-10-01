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
dates (join date required), valid non-empty email, and unlinked records.

`POST /api/v1/identity/accounts` accepts employee, initial workspace, one role,
effective and optional expiration times, and a note. Backend derives tenant and organization from
the authenticated administrator, checks the workspace and role policy, then atomically creates the
actor, account, initial membership, employee link, and activation challenge. Email is derived
from the locked Employee record, with syntax validation and lowercase/whitespace normalization. The request cannot
select permissions, scopes, data scope, or a password.

New accounts begin `ENABLED` and `PENDING`. Login remains denied until the employee activates with
a one-time expiring token and chooses a password. The database stores a challenge hash only. Challenge
creation does not imply delivery; `email_delivered` records the adapter outcome. Failed delivery
keeps the account PENDING and allows resend without creating a duplicate account.
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


## Operator commands and email configuration

Use `alos-admin bootstrap-identity --help` and `alos-admin import-employee --help`.
The equivalent module entrypoint is `python -m alos.cli`. Bootstrap takes operator-supplied
email and prompts for a password through getpass; it creates one fixed IT_ADMIN authority.
Employee import requires a valid email, normalized to lowercase and trimmed. Duplicate email
within a tenant/organization is rejected, including case and whitespace variants. Provisioning
clients must omit email; deploy updated Contracts, Backend, and Web together.

SMTP is provider-neutral. Set EMAIL_PROVIDER=smtp, EMAIL_FROM, EMAIL_FROM_NAME, SMTP_HOST,
SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS, SMTP_TIMEOUT_SECONDS, and APP_PUBLIC_URL.
There is no SMTP host or sender default. Staging/production SMTP configuration fails at Settings
construction if incomplete or invalid. Production APP_PUBLIC_URL rejects localhost and loopback.
SMTP_PASSWORD is canonical; SMTP_APP_PASSWORD is accepted temporarily as a legacy environment
alias only when SMTP_PASSWORD is absent. Migrate secrets to SMTP_PASSWORD before removing the alias.
Port 587 uses STARTTLS and port 465 uses implicit TLS when SMTP_USE_TLS=true.
Development/test can explicitly use EMAIL_PROVIDER=inmemory. Dummy senders are limited to the
in-memory adapter. Runtime SMTP failures leave accounts PENDING with email_delivered=false.

## Frozen audit vocabulary

- identity.initial_authority.bootstrapped
- identity.employee.imported
- identity.account.provisioned
- identity.activation.challenge_issued
- identity.activation.resent
- identity.account.activated (first activation)
- identity.account.suspended
- identity.account.reactivated (administrative reactivation)
- auth.password_reset.requested
- auth.password_reset.completed
- auth.password.changed
- identity.membership.granted
- identity.membership.updated
- identity.membership.revoked
- auth.session.revoked

Public password-reset audit uses entity_type=auth, entity_id=password_reset_request and
actor_id=anonymous for both known and unknown addresses. It records no email or credential
metadata. Historical audit rows retain their original names; newly emitted events use this list.

Migration 0028 already supplies a unique token_hash constraint (implicit PostgreSQL index) and
an account_id index for reset lookup/invalidation. It remains unchanged.
