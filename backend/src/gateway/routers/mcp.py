import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config.extensions_config import ExtensionsConfig, get_extensions_config, reload_extensions_config
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


@router.get(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Get MCP Configuration",
    description="Retrieve the current Model Context Protocol (MCP) server configurations.",
)
async def get_mcp_configuration(tenant_id: str = Depends(get_tenant_id)) -> McpConfigResponse:
    """Get the current MCP configuration (platform + tenant overlay)."""
    config = ExtensionsConfig.from_tenant(tenant_id)

    return McpConfigResponse(mcp_servers={name: McpServerConfigResponse(**server.model_dump()) for name, server in config.mcp_servers.items()})


@router.put(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Update MCP Configuration",
    description="Update Model Context Protocol (MCP) server configurations and save to file.",
    dependencies=[require_role("admin", "owner")],
)
async def update_mcp_configuration(request: McpConfigUpdateRequest, tenant_id: str = Depends(get_tenant_id)) -> McpConfigResponse:
    """Update the MCP configuration.

    This will:
    1. Save the new configuration to the mcp_config.json file
    2. Reload the configuration cache
    3. Reset MCP tools cache to trigger reinitialization

    Args:
        request: The new MCP configuration to save.

    Returns:
        The updated MCP configuration.

    Raises:
        HTTPException: 500 if the configuration file cannot be written.

    Example Request:
        ```json
        {
            "mcp_servers": {
                "github": {
                    "enabled": true,
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
                    "description": "GitHub MCP server for repository operations"
                }
            }
        }
        ```
    """
    try:
        # Determine write target: tenant overlay or platform-level
        if tenant_id and tenant_id != "default":
            from src.config.paths import get_paths
            tenant_dir = get_paths().tenant_dir(tenant_id)
            tenant_dir.mkdir(parents=True, exist_ok=True)
            config_path = tenant_dir / "extensions_config.json"
        else:
            config_path = ExtensionsConfig.resolve_config_path()
            if config_path is None:
                config_path = Path.cwd().parent / "extensions_config.json"
                logger.info(f"No existing extensions config found. Creating new config at: {config_path}")

        # Load current config to preserve skills configuration
        current_config = ExtensionsConfig.from_tenant(tenant_id)

        # Convert request to dict format for JSON serialization
        config_data = {
            "mcpServers": {name: server.model_dump() for name, server in request.mcp_servers.items()},
            "skills": {name: {"enabled": skill.enabled} for name, skill in current_config.skills.items()},
        }

        # Write the configuration to file
        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)

        logger.info(f"MCP configuration updated and saved to: {config_path}")

        # Reset MCP tools cache so next tool load picks up new config
        from src.mcp.cache import reset_mcp_tools_cache
        reset_mcp_tools_cache(tenant_id=tenant_id)

        # Reload platform-level config cache when updating default tenant
        if not tenant_id or tenant_id == "default":
            reload_extensions_config()

        # Reload the merged config for the response
        reloaded_config = ExtensionsConfig.from_tenant(tenant_id)
        return McpConfigResponse(mcp_servers={name: McpServerConfigResponse(**server.model_dump()) for name, server in reloaded_config.mcp_servers.items()})

    except Exception as e:
        logger.error(f"Failed to update MCP configuration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update MCP configuration: {str(e)}")
