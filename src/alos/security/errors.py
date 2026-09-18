"""Structured platform errors safe for API and integration boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from alos.observability.correlation import current_correlation_id


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
