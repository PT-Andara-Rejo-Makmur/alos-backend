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
from alos.api.public.ara_routes import router as ara_router
from alos.api.public.domain_routes import router as domain_data_router
from alos.api.public.domain_routes import workspace_navigation_router
from alos.api.public.executive_routes import router as executive_router
from alos.api.public.finance_routes import router as finance_router
from alos.api.public.hr_routes import router as hr_router
from alos.api.public.it_routes import router as it_router
from alos.api.public.legal_routes import router as legal_router
from alos.api.public.marketing_routes import router as marketing_router
from alos.api.public.property_routes import router as property_router
from alos.api.public.release_routes import router as release_router
from alos.api.public.routes import router as public_router
from alos.api.public.sales_routes import router as sales_router
from alos.api.public.shared_work_routes import router as shared_work_router
from alos.api.public.strategy_routes import router as strategy_router
from alos.audit import InMemoryAuditRepository, SqlAuditRepository, SqlToolAuditSink
from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.repository import SqlAuthRepository
from alos.authentication.service import AuthService
from alos.capabilities.registry import CapabilityRegistry
from alos.config import Settings, get_settings
from alos.contracts import CanonicalContractCatalog
from alos.domains.crud import DomainCrudService
from alos.domains.executive.service import ExecutiveProjectionService
from alos.domains.finance.service import FinanceService
from alos.domains.hr.service import HrService
from alos.domains.it.service import ItService
from alos.domains.legal.service import LegalService
from alos.domains.marketing.references import MarketingReferences
from alos.domains.marketing.service import MarketingService
from alos.domains.property.references import PropertyUnitReferences
from alos.domains.property.service import PropertyService
from alos.domains.record_repository import RecordRepository
from alos.domains.sales.references import SalesReferences
from alos.domains.sales.service import SalesService
from alos.domains.shared_work import SharedWorkService
from alos.domains.strategy import InMemoryStrategyRepository, SqlStrategyRepository, StrategyService
from alos.domains.strategy.repository import SqlStrategyAuditRepository
from alos.evidence import EvidenceRegistry, SqlEvidenceRegistry
from alos.integrations import ExternalRetrievalPolicy, ExternalRetrievalService
from alos.memory import MemoryService
from alos.notifications import InMemoryEmailAdapter, NotificationService, SmtpEmailAdapter
from alos.observability.correlation import CorrelationIdMiddleware
from alos.persistence.database import Database
from alos.persistence.registry import SqlRegistryStore
from alos.registry import InMemoryRegistryStore
from alos.releases import GovernedAgentLifecycle, PersistentReleaseAuthority
from alos.security.errors import install_error_handlers
from alos.security.rate_limit import InMemoryRateLimiter
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
        telemetry={"auto_configure": False},
    )
    app.state.settings = resolved
    app.state.started = False
    app.state.memory_service = MemoryService()
    app.state.database = Database(resolved.DATABASE_URL)
    app.state.domain_crud_service = DomainCrudService(app.state.database.session_factory)
    app.state.shared_work_service = SharedWorkService(app.state.database.session_factory)
    app.state.release_authority = PersistentReleaseAuthority(
        app.state.database.session_factory,
        registry_governed=True,
    )
    auth_repository = (
        InMemoryAuthRepository()
        if resolved.APP_ENV == "test"
        else SqlAuthRepository(app.state.database.session_factory)
    )
    app.state.test_activation_sink = {} if resolved.APP_ENV == "test" else None
    activation_sink = None
    if resolved.APP_ENV == "test":

        def store_test_activation(email: str, token: str) -> None:
            app.state.test_activation_sink[email] = token

        activation_sink = store_test_activation
    email_adapter = (
        InMemoryEmailAdapter()
        if resolved.APP_ENV == "test"
        or resolved.EMAIL_PROVIDER in {"test", "sink", "memory", "inmemory"}
        else SmtpEmailAdapter(resolved)
    )
    notification_service = NotificationService(
        settings=resolved,
        email_adapter=email_adapter,
        app_public_url=resolved.APP_PUBLIC_URL,
    )
    app.state.email_adapter = email_adapter
    app.state.notification_service = notification_service
    app.state.rate_limiter = InMemoryRateLimiter()

    app.state.auth_service = AuthService(
        auth_repository,
        session_ttl_minutes=resolved.AUTH_SESSION_TTL_MINUTES,
        activation_sink=activation_sink,
        notification_service=notification_service,
        activation_ttl_hours=resolved.ACTIVATION_TTL_HOURS,
        password_reset_ttl_minutes=resolved.PASSWORD_RESET_TTL_MINUTES,
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
            allow_test_drafts=resolved.APP_ENV == "test"
            or (resolved.APP_ENV == "development" and resolved.ENABLE_TEST_TOOLS),
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
    strategy_audit = (
        InMemoryAuditRepository()
        if resolved.APP_ENV == "test"
        else SqlAuditRepository(app.state.database.session_factory)
    )
    strategy_repository = (
        InMemoryStrategyRepository()
        if resolved.APP_ENV == "test"
        else SqlStrategyRepository(app.state.database.session_factory)
    )
    if isinstance(strategy_repository, SqlStrategyRepository):
        strategy_audit = SqlStrategyAuditRepository(strategy_repository)
    app.state.strategy_audit = strategy_audit
    app.state.strategy_repository = strategy_repository
    app.state.strategy_service = StrategyService(
        strategy_repository, strategy_audit, auth_repository.workspace
    )
    records = RecordRepository(app.state.database.session_factory, contracts)
    sales_refs = SalesReferences(records)
    marketing_refs = MarketingReferences(records)
    unit_refs = PropertyUnitReferences(records, app.state.shared_work_service)
    app.state.property_unit_references = unit_refs
    app.state.sales_service = SalesService(
        records,
        sales=sales_refs,
        marketing=marketing_refs,
        units=unit_refs,
        work=app.state.shared_work_service,
    )
    app.state.marketing_service = MarketingService(
        records, sales=sales_refs, marketing=marketing_refs
    )
    app.state.property_service = PropertyService(records, work=app.state.shared_work_service)
    app.state.finance_service = FinanceService(records, work=app.state.shared_work_service)
    app.state.it_service = ItService(records, work=app.state.shared_work_service)
    app.state.hr_service = HrService(records, work=app.state.shared_work_service)
    app.state.legal_service = LegalService(records, work=app.state.shared_work_service)
    app.state.shared_work_service.configure_material_approvals(
        contracts,
        {
            "SALES": app.state.sales_service,
            "PROPERTY": app.state.property_service,
            "FINANCE": app.state.finance_service,
        },
    )
    app.state.executive_service = ExecutiveProjectionService(
        app.state.strategy_service,
        app.state.shared_work_service,
        business_sources={
            "SALES": (
                ("sales", app.state.sales_service),
                ("marketing", app.state.marketing_service),
            ),
            "PROPERTY": (("property", app.state.property_service),),
            "FINANCE": (("finance", app.state.finance_service),),
            "IT": (("it", app.state.it_service),),
            "HR": (("hr", app.state.hr_service),),
            "LEGAL": (("legal", app.state.legal_service),),
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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
            email_configured=current.is_email_configured,
        )

    app.include_router(public_router)
    app.include_router(ara_router)
    app.include_router(domain_data_router)
    app.include_router(shared_work_router)
    app.include_router(workspace_navigation_router)
    app.include_router(release_router)
    app.include_router(strategy_router)
    app.include_router(executive_router)
    app.include_router(sales_router)
    app.include_router(marketing_router)
    app.include_router(property_router)
    app.include_router(finance_router)
    app.include_router(it_router)
    app.include_router(hr_router)
    app.include_router(legal_router)
    app.include_router(internal_router)
    return app


app = create_app()
