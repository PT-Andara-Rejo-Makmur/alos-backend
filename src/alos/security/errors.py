"""Structured platform errors safe for API and integration boundaries."""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from alos.observability.correlation import current_correlation_id

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def _safe_correlation_id(request: Request) -> str:
    """Resolve a correlation id for an error response without reflecting garbage.

    The generic handler runs in ServerErrorMiddleware, which sits outside the
    correlation middleware; the request context is already unwound there, so the
    propagated header is used as a sanitized fallback.
    """

    raw = request.headers.get("X-Correlation-ID")
    if isinstance(raw, str):
        candidate = raw.strip()
        if _CORRELATION_ID_PATTERN.match(candidate):
            return candidate
    return current_correlation_id()


class ProblemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    correlation_id: str
    retryable: bool = False
    details: dict[str, Any] | None = None


class PlatformError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details
        self.correlation_id = correlation_id


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        problem = ProblemDetail(
            code="REQUEST_VALIDATION_FAILED",
            message="Request does not satisfy the API input contract.",
            correlation_id=current_correlation_id(),
            details={"errors": jsonable_encoder(exc.errors())},
        )
        return JSONResponse(status_code=422, content=problem.model_dump(exclude_none=True))

    @app.exception_handler(PlatformError)
    async def handle_platform_error(_request: Request, exc: PlatformError) -> JSONResponse:
        problem = ProblemDetail(
            code=exc.code,
            message=exc.message,
            correlation_id=exc.correlation_id or current_correlation_id(),
            retryable=exc.retryable,
            details=exc.details,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=problem.model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Fail closed: internal failures never disclose SQL, stack traces, schema,
        credentials, or secrets to consumers."""

        del exc  # intentionally not serialized; see the frozen error contract
        problem = ProblemDetail(
            code="INTERNAL_PROCESSING_FAILURE",
            message=(
                "The request could not be completed safely. No internal details are disclosed."
            ),
            correlation_id=_safe_correlation_id(request),
            retryable=False,
        )
        return JSONResponse(status_code=500, content=problem.model_dump(exclude_none=True))
