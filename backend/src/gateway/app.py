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
from src.gateway.routers import agents, artifacts, governance, interventions, mcp, memory, models, runtime, skills, uploads
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

    # OIDC authentication middleware (Resource Server role).
    # Only mounted when OIDC_ENABLED=true. When disabled, all endpoints
    # remain open — matching the existing development workflow.
    oidc_config = load_oidc_config()
    if oidc_config.enabled:
        app.add_middleware(OIDCAuthMiddleware, config=oidc_config)
        logger.info("OIDC authentication enabled (issuer=%s, audience=%s)", oidc_config.issuer, oidc_config.audience)
    else:
        logger.info("OIDC authentication disabled (set OIDC_ENABLED=true to enable)")

    # Include routers
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

    # Runtime API is mounted at /api/runtime
    app.include_router(runtime.router)

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
