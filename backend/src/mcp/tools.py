"""Load MCP tools using langchain-mcp-adapters."""

import logging

from langchain_core.tools import BaseTool

from src.config.extensions_config import ExtensionsConfig
from src.mcp.client import build_servers_config
from src.mcp.oauth import build_oauth_tool_interceptor, get_initial_oauth_headers

logger = logging.getLogger(__name__)


async def get_mcp_tools(tenant_id: str | None = None, user_id: str | None = None) -> list[BaseTool]:
    """Get all tools from enabled MCP servers.

    Args:
        tenant_id: When provided and not "default", loads the tenant-overlay
                   extensions config (merged on top of the platform config).
        user_id: When provided and not "anonymous", loads the user-overlay
                 extensions config (merged on top of the tenant config).

    Returns:
        List of LangChain tools from all enabled MCP servers.
    """
    try:
        # Patch MCP SDK on Windows if needed (locked-down pipe creation).
        from src.mcp.win32_stdio_fallback import apply_win32_stdio_fallback_patch

        apply_win32_stdio_fallback_patch()
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters not installed. Install it to enable MCP tools: pip install langchain-mcp-adapters")
        return []

    # Load config with tenant + user overlay when applicable.
    extensions_config = ExtensionsConfig.from_user(tenant_id, user_id)
    servers_config = build_servers_config(extensions_config)

    if not servers_config:
        logger.info("No enabled MCP servers configured")
        return []

    try:
        # Create the multi-server MCP client
        logger.info(f"Initializing MCP client with {len(servers_config)} server(s)")

        # Inject initial OAuth headers for server connections (tool discovery/session init)
        initial_oauth_headers = await get_initial_oauth_headers(extensions_config)
        for server_name, auth_header in initial_oauth_headers.items():
            if server_name not in servers_config:
                continue
            if servers_config[server_name].get("transport") in ("sse", "http"):
                existing_headers = dict(servers_config[server_name].get("headers", {}))
                existing_headers["Authorization"] = auth_header
                servers_config[server_name]["headers"] = existing_headers

        tool_interceptors = []
        oauth_interceptor = build_oauth_tool_interceptor(extensions_config)
        if oauth_interceptor is not None:
            tool_interceptors.append(oauth_interceptor)

        client = MultiServerMCPClient(servers_config, tool_interceptors=tool_interceptors)

        # Get all tools from all servers
        tools = await client.get_tools()
        logger.info(f"Successfully loaded {len(tools)} tool(s) from MCP servers")

        return tools

    except Exception as e:
        logger.error(f"Failed to load MCP tools: {e}", exc_info=True)
        return []
