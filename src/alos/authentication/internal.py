"""Constant-time internal service token authentication."""

import secrets
from typing import Annotated

from fastapi import Header, Request

from alos.security.errors import PlatformError


async def verify_internal_token(
    request: Request,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    configured = request.app.state.settings.GENESIS_INTERNAL_TOKEN.get_secret_value()
    if (
        not configured
        or not x_internal_token
        or not secrets.compare_digest(configured, x_internal_token)
    ):
        raise PlatformError(
            "INTERNAL_AUTH_DENIED",
            "Internal service authentication failed.",
            status_code=401,
        )
