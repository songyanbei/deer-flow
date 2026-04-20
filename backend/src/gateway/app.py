import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config.app_config import get_app_config
from src.gateway.config import get_gateway_config
from src.gateway.middleware.oidc import OIDCAuthMiddleware
from src.gateway.middleware.oidc_config import load_oidc_config
from src.gateway.sso.config import get_sso_config, reset_sso_config_cache
from src.admin.router import router as admin_router
from src.gateway.routers import agents, artifacts, governance, interventions, mcp, me, memory, models, promotions, runtime, skills, uploads
from src.gateway.routers import sso as sso_router
from src.observability import WorkflowMetrics, init_observability

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""

    # Load config and check necessary environment variables at startup
    try:
        get_app_config()
        # Validate SSO config eagerly so bad env values fail-fast instead of
        # surfacing on first /api/sso/callback request.  ``get_sso_config``
        # caches the result so ``create_app`` below reuses the same instance.
        reset_sso_config_cache()
        get_sso_config()
        logger.info("Configuration loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    # Initialize observability (decision logger + optional OTel)
    init_observability()

    config = get_gateway_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # NOTE: MCP tools initialization is NOT done here because:
    # 1. Gateway doesn't use MCP tools - they are used by Agents in the LangGraph Server
    # 2. Gateway and LangGraph Server are separate processes with independent caches
    # MCP tools are lazily initialized in LangGraph Server when first needed

    yield
    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """

    app = FastAPI(
        title="DeerFlow API Gateway",
        description="""
## DeerFlow API Gateway

API Gateway for DeerFlow - A LangGraph-based AI agent backend with sandbox execution capabilities.

### Features

- **Models Management**: Query and retrieve available AI models
- **MCP Configuration**: Manage Model Context Protocol (MCP) server configurations
- **Memory Management**: Access and manage global memory data for personalized conversations
- **Skills Management**: Query and manage skills and their enabled status
- **Artifacts**: Access thread artifacts and generated files
- **Health Monitoring**: System health check endpoints

### Architecture

LangGraph requests are handled by nginx reverse proxy.
This gateway provides custom endpoints for models, MCP configuration, skills, and artifacts.
        """,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {
                "name": "models",
                "description": "Operations for querying available AI models and their configurations",
            },
            {
                "name": "mcp",
                "description": "Manage Model Context Protocol (MCP) server configurations",
            },
            {
                "name": "memory",
                "description": "Access and manage global memory data for personalized conversations",
            },
            {
                "name": "skills",
                "description": "Manage skills and their configurations",
            },
            {
                "name": "artifacts",
                "description": "Access and download thread artifacts and generated files",
            },
            {
                "name": "uploads",
                "description": "Upload and manage user files for threads",
            },
            {
                "name": "agents",
                "description": "Create and manage custom agents with per-agent config and prompts",
            },
            {
                "name": "runtime",
                "description": "Platform runtime adapter for thread creation, state query, and message streaming",
            },
            {
                "name": "health",
                "description": "Health check and system status endpoints",
            },
        ],
    )

    config = get_gateway_config()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth middleware — covers both OIDC bearer tokens and moss-hub SSO
    # cookies. Mounted whenever either auth mode is enabled; the middleware
    # routes by JWT ``kid`` internally.
    oidc_config = load_oidc_config()
    sso_config = get_sso_config()
    if oidc_config.enabled or sso_config.enabled:
        app.add_middleware(
            OIDCAuthMiddleware,
            config=oidc_config,
            sso_config=sso_config if sso_config.enabled else None,
        )
        logger.info(
            "Auth middleware enabled (oidc=%s, sso=%s, tenant=%s)",
            oidc_config.enabled,
            sso_config.enabled,
            sso_config.tenant_id if sso_config.enabled else None,
        )
    else:
        logger.info(
            "Auth middleware disabled (set OIDC_ENABLED=true or SSO_ENABLED=true to enable)"
        )

    # Include routers
    # SSO callback is mounted at /api/sso/callback (exempt from auth)
    app.include_router(sso_router.router)

    # Models API is mounted at /api/models
    app.include_router(models.router)

    # MCP API is mounted at /api/mcp
    app.include_router(mcp.router)

    # Memory API is mounted at /api/memory
    app.include_router(memory.router)

    # Skills API is mounted at /api/skills
    app.include_router(skills.router)

    # Artifacts API is mounted at /api/threads/{thread_id}/artifacts
    app.include_router(artifacts.router)

    # Uploads API is mounted at /api/threads/{thread_id}/uploads
    app.include_router(uploads.router)

    # Agents API is mounted at /api/agents
    app.include_router(agents.router)

    # Interventions API is mounted at /api/threads/{thread_id}/interventions
    app.include_router(interventions.router)

    # Governance API is mounted at /api/governance
    app.include_router(governance.router)

    # Personal resource API is mounted at /api/me
    app.include_router(me.router)

    # Promotion submit endpoints are mounted at /api/me (agents/skills :promote)
    app.include_router(promotions.me_router)

    # Promotion admin endpoints are mounted at /api/promotions
    app.include_router(promotions.router)

    # Runtime API is mounted at /api/runtime
    app.include_router(runtime.router)

    # Admin API is mounted at /api/admin
    app.include_router(admin_router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        """Health check endpoint.

        Returns:
            Service health status information.
        """
        return {"status": "healthy", "service": "deer-flow-gateway"}

    @app.get("/debug/metrics", tags=["health"])
    async def debug_metrics() -> dict:
        """Debug metrics endpoint — returns in-memory metrics snapshot."""
        return WorkflowMetrics.get().snapshot()

    return app


# Create app instance for uvicorn
app = create_app()
