"""Revalidate current membership at every process read and mutation boundary."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository
from alos.identity import Principal
from alos.security.errors import PlatformError


async def revalidate(
    repository: RecordRepository, session: AsyncSession, principal: Principal
) -> None:
    await repository.workspace(session, principal)
    members = await repository.table(session, "core", "workspace_memberships")
    actors = await repository.table(session, "core", "actors")
    accounts = await repository.table(session, "core", "auth_accounts")
    now = datetime.now(UTC)
    member = (
        (
            await session.execute(
                select(members)
                .where(
                    *repository.scope(members, principal),
                    members.c.actor_id == principal.actor_id,
                    members.c.active.is_(True),
                    members.c.revoked_at.is_(None),
                    members.c.effective_at <= now,
                    (members.c.expires_at.is_(None) | (members.c.expires_at > now)),
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    active_actor = await session.scalar(
        select(actors.c.actor_id).where(
            actors.c.actor_id == principal.actor_id,
            actors.c.tenant_id == principal.tenant_id,
            actors.c.organization_id == principal.organization_id,
            actors.c.active.is_(True),
        )
    )
    active_account = await session.scalar(
        select(accounts.c.actor_id).where(
            accounts.c.actor_id == principal.actor_id,
            accounts.c.tenant_id == principal.tenant_id,
            accounts.c.organization_id == principal.organization_id,
            accounts.c.active.is_(True),
            accounts.c.administrative_state == "ENABLED",
            accounts.c.activation_state == "ACTIVATED",
        )
    )
    if (
        not principal.active
        or member is None
        or active_actor is None
        or active_account is None
        or set(member["roles"]) != set(principal.roles)
        or not principal.permissions.issubset(member["permission_refs"])
    ):
        raise PlatformError(
            "PROCESS_AUTHORITY_DENIED", "Kewenangan aktif telah berubah.", status_code=403
        )


def assigned(step: dict[str, Any], principal: Principal) -> bool:
    return (
        step["workspace_id"] == principal.workspace_id
        and step["role"] in principal.roles
        and step["permission"] in principal.permissions
    )
