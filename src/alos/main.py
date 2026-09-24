"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from alos.agents.lifecycle import AgentRunAuthority, SqlAgentRunStore
from alos.agents.lifecycle.repository import SqlAgentRunStepStore
from alos.agents.registry import AgentRegistry
from alos.api.internal.routes import router as internal_router
from alos.api.models import HealthResponse, ReadinessResponse
from alos.api.public.release_routes import router as release_router
from alos.api.public.routes import router as public_router
from alos.audit import InMemoryAuditRepository, SqlAuditRepository, SqlToolAuditSink
from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.repository import SqlAuthRepository
from alos.authentication.service import AuthService
from alos.capabilities.registry import CapabilityRegistry
from alos.config import Settings, get_settings
from alos.contracts import CanonicalContractCatalog
from alos.evidence import EvidenceRegistry, SqlEvidenceRegistry
from alos.integrations import ExternalRetrievalPolicy, ExternalRetrievalService
from alos.observability.correlation import CorrelationIdMiddleware
from alos.persistence.database import Database
from alos.persistence.registry import SqlRegistryStore
from alos.registry import InMemoryRegistryStore
from alos.releases import GovernedAgentLifecycle, PersistentReleaseAuthority
from alos.security.errors import install_error_handlers
from alos.skills.registry import SkillRegistry
from alos.tools.executor.service import (
    InMemoryToolAuditSink,
    InMemoryToolIdempotencyStore,
    SqlToolIdempotencyStore,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started = True
        if not app.state.registry_hydrated:
            if isinstance(app.state.agent_registry, AgentRegistry):
                await app.state.agent_registry.hydrate()
            if isinstance(app.state.skill_registry, SkillRegistry):
                await app.state.skill_registry.hydrate()
            app.state.registry_hydrated = True
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
    app.state.database = Database(resolved.DATABASE_URL)
    app.state.release_authority = PersistentReleaseAuthority(
        app.state.database.session_factory,
        registry_governed=True,
    )
    auth_repository = (
        InMemoryAuthRepository()
        if resolved.APP_ENV == "test"
        else SqlAuthRepository(app.state.database.session_factory)
    )
    app.state.auth_service = AuthService(
        auth_repository, session_ttl_minutes=resolved.AUTH_SESSION_TTL_MINUTES
    )
    app.state.identity_audit = (
        InMemoryAuditRepository()
        if resolved.APP_ENV == "test"
        else SqlAuditRepository(app.state.database.session_factory)
    )
    app.state.registry_store = (
        InMemoryRegistryStore()
        if resolved.APP_ENV == "test"
        else SqlRegistryStore(app.state.database.session_factory)
    )
    registry_audit = (
        SqlAuditRepository(app.state.database.session_factory)
        if resolved.APP_ENV in {"staging", "production"}
        else InMemoryAuditRepository()
    )
    contracts = (
        CanonicalContractCatalog(resolved.ALOS_CONTRACTS_PATH)
        if resolved.ALOS_CONTRACTS_PATH is not None
        else None
    )
    agent_registry = (
        AgentRegistry(
            contracts,
            registry_audit,
            store=app.state.registry_store,
            release_governed=True,
        )
        if contracts is not None
        else None
    )
    app.state.factory_contracts = contracts
    app.state.evidence_registry = (
        (
            EvidenceRegistry(contracts)
            if resolved.APP_ENV == "test"
            else SqlEvidenceRegistry(contracts, app.state.database.session_factory)
        )
        if contracts is not None
        else None
    )
    app.state.factory_capability_registry = (
        CapabilityRegistry(contracts, registry_audit) if contracts is not None else None
    )
    app.state.factory_agent_registry = agent_registry
    app.state.agent_lifecycle = (
        GovernedAgentLifecycle(app.state.database.session_factory, agent_registry)
        if agent_registry is not None
        else None
    )
    app.state.factory_registry_audit = registry_audit
    app.state.skill_registry = (
        SkillRegistry(contracts, registry_audit, store=app.state.registry_store)
        if contracts is not None
        else None
    )
    app.state.agent_run_authority = (
        AgentRunAuthority(
            contracts=contracts,
            audit=registry_audit,
            skills=app.state.skill_registry,
            allow_test_drafts=resolved.APP_ENV == "test",
            store=(
                None
                if resolved.APP_ENV == "test"
                else SqlAgentRunStore(app.state.database.session_factory)
            ),
            step_store=(
                None
                if resolved.APP_ENV == "test"
                else SqlAgentRunStepStore(app.state.database.session_factory)
            ),
        )
        if contracts is not None
        else None
    )
    app.state.skill_service = None
    app.state.skill_audit = registry_audit
    app.state.agent_registry = agent_registry
    app.state.registry_hydrated = False
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
    app.state.tool_audit_sink = (
        InMemoryToolAuditSink()
        if resolved.APP_ENV == "test"
        else SqlToolAuditSink(SqlAuditRepository(app.state.database.session_factory))
    )
    app.state.tool_idempotency_store = (
        InMemoryToolIdempotencyStore()
        if resolved.APP_ENV == "test"
        else SqlToolIdempotencyStore(app.state.database.session_factory)
    )
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
    app.include_router(release_router)
    app.include_router(internal_router)
    return app


app = create_app()
