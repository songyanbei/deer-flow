import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.config._config_lock import atomic_write_json, tenant_config_lock
from src.config.extensions_config import ExtensionsConfig, reload_extensions_config
from src.gateway.dependencies import get_tenant_id, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mcp"])


class McpOAuthConfigResponse(BaseModel):
    """OAuth configuration for an MCP server."""

    enabled: bool = Field(default=True, description="Whether OAuth token injection is enabled")
    token_url: str = Field(default="", description="OAuth token endpoint URL")
    grant_type: Literal["client_credentials", "refresh_token"] = Field(default="client_credentials", description="OAuth grant type")
    client_id: str | None = Field(default=None, description="OAuth client ID")
    client_secret: str | None = Field(default=None, description="OAuth client secret")
    refresh_token: str | None = Field(default=None, description="OAuth refresh token")
    scope: str | None = Field(default=None, description="OAuth scope")
    audience: str | None = Field(default=None, description="OAuth audience")
    token_field: str = Field(default="access_token", description="Token response field containing access token")
    token_type_field: str = Field(default="token_type", description="Token response field containing token type")
    expires_in_field: str = Field(default="expires_in", description="Token response field containing expires-in seconds")
    default_token_type: str = Field(default="Bearer", description="Default token type when response omits token_type")
    refresh_skew_seconds: int = Field(default=60, description="Refresh this many seconds before expiry")
    extra_token_params: dict[str, str] = Field(default_factory=dict, description="Additional form params sent to token endpoint")


class McpServerConfigResponse(BaseModel):
    """Response model for MCP server configuration."""

    enabled: bool = Field(default=True, description="Whether this MCP server is enabled")
    type: str = Field(default="stdio", description="Transport type: 'stdio', 'sse', or 'http'")
    command: str | None = Field(default=None, description="Command to execute to start the MCP server (for stdio type)")
    args: list[str] = Field(default_factory=list, description="Arguments to pass to the command (for stdio type)")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for the MCP server")
    url: str | None = Field(default=None, description="URL of the MCP server (for sse or http type)")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers to send (for sse or http type)")
    oauth: McpOAuthConfigResponse | None = Field(default=None, description="OAuth configuration for MCP HTTP/SSE servers")
    description: str = Field(default="", description="Human-readable description of what this MCP server provides")

    # Light-management fields (new, optional for backward compatibility)
    healthcheck_path: str | None = Field(default=None, description="Health check endpoint path for sse/http servers")
    connect_timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")
    call_timeout_seconds: int = Field(default=60, description="Tool call timeout in seconds")
    retry_count: int = Field(default=0, description="Number of retries on transient failures")
    circuit_breaker_enabled: bool = Field(default=False, description="Enable circuit breaker for repeated failures")
    category: str = Field(default="global", description="MCP server category: global, domain, shared, or ephemeral")
    domain: str | None = Field(default=None, description="Domain label when category='domain'")
    readonly: bool = Field(default=False, description="If True, write-like tools are filtered for read-only agents")
    source: str | None = Field(default=None, description="Origin that provisioned this entry (e.g. 'moss-portal')")
    mcp_kind: str | None = Field(default=None, description="'local' or 'remote' — transparent pass-through for platform filtering")


class McpConfigResponse(BaseModel):
    """Response model for MCP configuration."""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(
        default_factory=dict,
        description="Map of MCP server name to configuration",
    )


class McpConfigUpdateRequest(BaseModel):
    """Request model for updating MCP configuration."""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(
        ...,
        description="Map of MCP server name to configuration",
    )


class McpSingleItemUpdateResponse(BaseModel):
    """Response for single-item PUT."""

    name: str
    source: str | None = None
    updated_at: str


# ── Internal helpers ──────────────────────────────────────────────────


def _resolve_mcp_config_path(tenant_id: str) -> Path:
    """Return the extensions_config.json path for the given tenant."""
    if tenant_id and tenant_id != "default":
        from src.config.paths import get_paths
        return get_paths().tenant_dir(tenant_id) / "extensions_config.json"
    config_path = ExtensionsConfig.resolve_config_path()
    if config_path is None:
        config_path = Path.cwd().parent / "extensions_config.json"
    return config_path


def _mcp_lockfile(tenant_id: str) -> Path:
    """Return the advisory lock file for MCP config writes."""
    return _resolve_mcp_config_path(tenant_id).parent / ".mcp.lock"


def _load_raw_config(config_path: Path) -> dict:
    """Load the raw JSON config dict from disk (or empty dict if absent)."""
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_and_invalidate(config_path: Path, config_data: dict, tenant_id: str) -> None:
    """Atomically write config and invalidate caches."""
    atomic_write_json(config_path, config_data)
    logger.info("MCP configuration saved to: %s", config_path)

    from src.mcp.cache import reset_mcp_tools_cache
    reset_mcp_tools_cache(tenant_id=tenant_id)

    if not tenant_id or tenant_id == "default":
        reload_extensions_config()


def _merged_response(tenant_id: str) -> McpConfigResponse:
    """Build a McpConfigResponse from the freshly merged config."""
    config = ExtensionsConfig.from_tenant(tenant_id)
    return McpConfigResponse(mcp_servers={name: McpServerConfigResponse(**server.model_dump()) for name, server in config.mcp_servers.items()})


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Get MCP Configuration",
    description="Retrieve the current Model Context Protocol (MCP) server configurations.",
)
async def get_mcp_configuration(tenant_id: str = Depends(get_tenant_id)) -> McpConfigResponse:
    """Get the current MCP configuration (platform + tenant overlay)."""
    return _merged_response(tenant_id)


@router.get(
    "/mcp/config/{name}",
    response_model=McpServerConfigResponse,
    summary="Get Single MCP Server",
    description="Retrieve configuration for a single MCP server by name.",
)
async def get_mcp_server(name: str, tenant_id: str = Depends(get_tenant_id)) -> McpServerConfigResponse:
    """Get a single MCP server configuration by name."""
    config = ExtensionsConfig.from_tenant(tenant_id)
    server = config.mcp_servers.get(name)
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return McpServerConfigResponse(**server.model_dump())


@router.put(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Update MCP Configuration (full replace)",
    description="Update Model Context Protocol (MCP) server configurations and save to file.",
    dependencies=[require_role("admin", "owner")],
)
async def update_mcp_configuration(request: McpConfigUpdateRequest, tenant_id: str = Depends(get_tenant_id)) -> McpConfigResponse:
    """Replace the entire MCP server table (preserving skills section)."""
    try:
        config_path = _resolve_mcp_config_path(tenant_id)

        async with tenant_config_lock(tenant_id, "mcp", lockfile=_mcp_lockfile(tenant_id)):
            raw = _load_raw_config(config_path)

            config_data = {
                "mcpServers": {name: server.model_dump() for name, server in request.mcp_servers.items()},
                "skills": raw.get("skills", {}),
            }

            _save_and_invalidate(config_path, config_data, tenant_id)

        return _merged_response(tenant_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update MCP configuration: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update MCP configuration: {str(e)}")


@router.put(
    "/mcp/config/{name}",
    response_model=McpSingleItemUpdateResponse,
    summary="Update Single MCP Server",
    description="Create or update a single MCP server entry by name.",
    dependencies=[require_role("admin", "owner")],
)
async def update_mcp_server(
    name: str,
    body: McpServerConfigResponse,
    tenant_id: str = Depends(get_tenant_id),
) -> McpSingleItemUpdateResponse:
    """Single-item upsert for one MCP server.

    If an entry with *name* already exists and its ``source`` differs from
    the request body's ``source``, the request is rejected with 409 to
    prevent cross-owner overwrites.
    """
    try:
        config_path = _resolve_mcp_config_path(tenant_id)

        async with tenant_config_lock(tenant_id, "mcp", lockfile=_mcp_lockfile(tenant_id)):
            raw = _load_raw_config(config_path)
            servers = raw.get("mcpServers", {})

            existing = servers.get(name)
            if existing is not None:
                existing_source = existing.get("source")
                incoming_source = body.source
                if existing_source:
                    if not incoming_source:
                        raise HTTPException(
                            status_code=409,
                            detail=f"MCP '{name}' is managed by '{existing_source}'; must provide matching source to update",
                        )
                    if existing_source != incoming_source:
                        raise HTTPException(
                            status_code=409,
                            detail=f"MCP '{name}' is managed by '{existing_source}'; cannot override with source '{incoming_source}'",
                        )

            servers[name] = body.model_dump()
            raw["mcpServers"] = servers
            _save_and_invalidate(config_path, raw, tenant_id)

        now = datetime.now(UTC).isoformat()
        return McpSingleItemUpdateResponse(name=name, source=body.source, updated_at=now)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update MCP server '%s': %s", name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update MCP server: {str(e)}")


@router.delete(
    "/mcp/config/{name}",
    status_code=200,
    summary="Delete Single MCP Server",
    description="Delete a single MCP server entry by name. Source ownership is enforced.",
    dependencies=[require_role("admin", "owner")],
)
async def delete_mcp_server(
    name: str,
    tenant_id: str = Depends(get_tenant_id),
    source: str | None = Query(default=None, description="Source tag to match against the existing entry"),
) -> dict:
    """Delete one MCP server entry.

    When *source* is provided, the existing entry's ``source`` must match.
    When *source* is omitted, deletion is allowed only for entries with
    ``source=None`` (i.e. manually created).
    """
    try:
        config_path = _resolve_mcp_config_path(tenant_id)

        async with tenant_config_lock(tenant_id, "mcp", lockfile=_mcp_lockfile(tenant_id)):
            raw = _load_raw_config(config_path)
            servers = raw.get("mcpServers", {})

            if name not in servers:
                raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

            existing_source = servers[name].get("source")

            if source is not None:
                if existing_source and existing_source != source:
                    raise HTTPException(
                        status_code=409,
                        detail=f"MCP '{name}' is managed by '{existing_source}'; cannot delete with source '{source}'",
                    )
            else:
                if existing_source:
                    raise HTTPException(
                        status_code=403,
                        detail=f"MCP '{name}' is managed by '{existing_source}'; provide matching source or admin scope to delete",
                    )

            del servers[name]
            raw["mcpServers"] = servers
            _save_and_invalidate(config_path, raw, tenant_id)

        return {"deleted": name}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete MCP server '%s': %s", name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete MCP server: {str(e)}")
