"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from alos.api.internal.routes import router as internal_router
from alos.api.models import HealthResponse, ReadinessResponse
from alos.api.public.routes import router as public_router
from alos.audit import InMemoryAuditRepository
from alos.authentication.service import AuthService
from alos.config import Settings, get_settings
from alos.integrations import ExternalRetrievalPolicy, ExternalRetrievalService
from alos.observability.correlation import CorrelationIdMiddleware
from alos.persistence.database import Database
from alos.persistence.registry import SqlRegistryStore
from alos.registry import InMemoryRegistryStore
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
        try:
            yield
        finally:
            await app.state.external_retrieval_service.close()
            await app.state.database.dispose()
            app.state.started = False

    app = FastAPI(
        title="ALOS Backend",
        version="0.1.0",
        description="Authoritative core platform API for ALOS.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.started = False
    app.state.auth_service = AuthService()
    app.state.database = Database(resolved.DATABASE_URL)
    app.state.registry_store = (
        InMemoryRegistryStore()
        if resolved.APP_ENV == "test"
        else SqlRegistryStore(app.state.database.session_factory)
    )
    app.state.factory_contracts = None
    app.state.factory_capability_registry = None
    app.state.factory_agent_registry = None
    app.state.factory_registry_audit = None
    app.state.skill_registry = None
    app.state.skill_service = None
    app.state.agent_registry = None
    app.state.external_retrieval_audit = InMemoryAuditRepository()
    app.state.research_audit = InMemoryAuditRepository()
    app.state.external_retrieval_service = ExternalRetrievalService(
        policy=ExternalRetrievalPolicy(
            allowed_protocols=resolved.egress_allowed_protocols,
            allowed_domains=resolved.egress_allowed_domains,
            timeout_seconds=resolved.EGRESS_TIMEOUT_SECONDS,
            max_response_bytes=resolved.EGRESS_MAX_RESPONSE_BYTES,
            allowed_content_types=resolved.egress_allowed_content_types,
            block_private_networks=resolved.EGRESS_BLOCK_PRIVATE_NETWORKS,
        ),
        audit=app.state.external_retrieval_audit,
    )
    app.state.tool_audit_sink = InMemoryToolAuditSink()
    app.state.tool_idempotency_store = InMemoryToolIdempotencyStore()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type", "X-Correlation-ID"],
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
