# Initial identity administrator bootstrap

Production self-registration is disabled. The first identity administrator is
created only through the server-side `alos-admin` command after migrations have
been applied.

```text
alos-admin bootstrap-identity \
  --email <operator-supplied-email> \
  --display-name <operator-supplied-name> \
  --tenant-id <tenant-id> \
  --organization-id <organization-id> \
  --workspace-id <workspace-id> \
  --workspace-key <workspace-key> \
  --workspace-name <workspace-name>
```

Set `DATABASE_URL` in the server environment. The command prompts twice for
the password through a masked terminal prompt; no password flag exists and the
password is never printed or written to audit metadata.

The operation creates exactly one initial `IT_ADMIN` authority with the
identity account and membership management permissions needed to provision
subsequent accounts. PostgreSQL serializes concurrent attempts, and the command
fails with `IDENTITY_BOOTSTRAP_EXISTS` when an active initial identity authority
already exists. There is intentionally no routine override and no public HTTP
bootstrap endpoint.

The success audit event records actor and boundary identifiers plus the
canonical role only. Tenant, organization, workspace, email, display name, and
password are never hardcoded by the application.
