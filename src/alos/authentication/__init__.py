"""Authentication boundaries. Identity storage is intentionally separate."""

from alos.authentication.models import (
    AuthenticatedPrincipalProjection,
    AuthTokenResponse,
    LoginRequest,
    RegisterRequest,
)
from alos.authentication.service import AuthService

__all__ = [
    "AuthService",
    "AuthTokenResponse",
    "AuthenticatedPrincipalProjection",
    "LoginRequest",
    "RegisterRequest",
]
