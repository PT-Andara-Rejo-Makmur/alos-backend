"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from alos.api.internal.routes import router as internal_router
from alos.api.models import HealthResponse, ReadinessResponse
from alos.api.public.routes import router as public_router
from alos.config import Settings, get_settings
from alos.observability.correlation import CorrelationIdMiddleware
from alos.security.errors import install_error_handlers
from alos.tools.executor.service import (
    InMemoryToolAuditSink,
    InMemoryToolIdempotencyStore,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started = True
        yield
        app.state.started = False

    app = FastAPI(
        title="ALOS Backend",
        version="0.1.0",
        description="Authoritative core platform API for ALOS.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.started = False
    app.state.tool_audit_sink = InMemoryToolAuditSink()
    app.state.tool_idempotency_store = InMemoryToolIdempotencyStore()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )
    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/ready", response_model=ReadinessResponse, tags=["system"])
    async def ready(request: Request) -> ReadinessResponse:
        current = request.app.state.settings
        return ReadinessResponse(
            database_configured=current.DATABASE_URL.startswith("postgresql+asyncpg://"),
            genesis_configured=bool(current.GENESIS_BASE_URL),
        )

    app.include_router(public_router)
    app.include_router(internal_router)
    return app


app = create_app()
