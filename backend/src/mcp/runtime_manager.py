"""Unified MCP runtime manager.

Replaces the dual-path architecture (``mcp/cache.py`` for global tools +
``execution/mcp_pool.py`` for domain agents) with a single, scope-aware
runtime that supports all transport types and enforces isolation.

Scope keys
----------
- ``"global"``                 – platform-wide MCP servers.
- ``"domain:<agent_name>"``    – domain-agent scoped servers.
- ``"run:<run_id>"``           – per-run ephemeral scope (reserved).

Usage
-----
::

    from src.mcp.runtime_manager import mcp_runtime

    # Global tools (main agent)
    tools = await mcp_runtime.get_tools("global")

    # Domain agent tools
    await mcp_runtime.load_scope("domain:contacts-agent", server_configs)
    tools = await mcp_runtime.get_tools("domain:contacts-agent")

    # Shutdown
    await mcp_runtime.shutdown()
"""

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool

from src.config.extensions_config import McpServerConfig
from src.mcp.client import build_server_params
from src.mcp.health import check_server_health

logger = logging.getLogger(__name__)


class _ScopedMCPClient:
    """Manages MCP connections for a single scope (global or domain:<name>)."""

    def __init__(self, scope_key: str, servers: dict[str, McpServerConfig]):
        self.scope_key = scope_key
        self._servers = servers
        self._tools: list[BaseTool] | None = None
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def connect(self) -> bool:
        """Establish connections to all configured MCP servers in this scope."""
        async with self._lock:
            if self._tools is not None and self._last_error is None:
                return True

            self._last_error = None

            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
            except ImportError:
                self._last_error = "langchain-mcp-adapters not installed"
                logger.error("[McpRuntime] %s. Run: uv add langchain-mcp-adapters", self._last_error)
                return False

            # Optional health checks for SSE/HTTP servers (non-blocking)
            for srv_name, srv_cfg in self._servers.items():
                if srv_cfg.type in ("sse", "http"):
                    healthy = await check_server_health(srv_name, srv_cfg)
                    if not healthy:
                        logger.warning("[McpRuntime] Scope '%s': server '%s' is unhealthy, will attempt connection anyway.", self.scope_key, srv_name)

            # Build params dict for MultiServerMCPClient
            server_params: dict[str, dict[str, Any]] = {}
            for srv_name, srv_cfg in self._servers.items():
                try:
                    server_params[srv_name] = build_server_params(srv_name, srv_cfg)
                except Exception as e:
                    logger.error("[McpRuntime] Scope '%s': failed to build params for '%s': %s", self.scope_key, srv_name, e)

            if not server_params:
                if self._servers:
                    self._last_error = "no valid server configs"
                    logger.warning("[McpRuntime] Scope '%s': %s.", self.scope_key, self._last_error)
                    return False
                self._tools = []
                return True

            # Inject OAuth headers if needed
            try:
                from src.config.extensions_config import ExtensionsConfig
                from src.mcp.oauth import build_oauth_tool_interceptor, get_initial_oauth_headers

                # Build a temporary ExtensionsConfig with only the servers in scope
                temp_cfg = ExtensionsConfig(mcp_servers=self._servers)
                initial_headers = await get_initial_oauth_headers(temp_cfg)
                for srv_name, auth_header in initial_headers.items():
                    if srv_name in server_params and server_params[srv_name].get("transport") in ("sse", "http"):
                        existing = dict(server_params[srv_name].get("headers", {}))
                        existing["Authorization"] = auth_header
                        server_params[srv_name]["headers"] = existing

                tool_interceptors = []
                oauth_interceptor = build_oauth_tool_interceptor(temp_cfg)
                if oauth_interceptor is not None:
                    tool_interceptors.append(oauth_interceptor)
            except Exception as e:
                logger.debug("[McpRuntime] OAuth setup skipped for scope '%s': %s", self.scope_key, e)
                tool_interceptors = []

            try:
                self._client = MultiServerMCPClient(server_params, tool_interceptors=tool_interceptors)
                self._tools = await self._client.get_tools()
                self._last_error = None
                logger.info(
                    "[McpRuntime] Scope '%s': connected %d server(s), loaded %d tool(s).",
                    self.scope_key,
                    len(server_params),
                    len(self._tools),
                )
                return True
            except Exception as e:
                self._last_error = str(e)
                logger.error("[McpRuntime] Scope '%s': connection failed: %s", self.scope_key, e)
                self._tools = None
                return False

    async def get_tools(self) -> list[BaseTool]:
        if self._tools is None:
            success = await self.connect()
            if not success:
                return []
        return self._tools or []

    def get_tools_sync(self) -> list[BaseTool]:
        """Return cached tools synchronously (avoids serialization issues during checkpointing)."""
        return self._tools or []

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.__aexit__(None, None, None), timeout=5.0)
            except Exception as e:
                logger.warning("[McpRuntime] Scope '%s': disconnect warning: %s", self.scope_key, e)
            finally:
                self._client = None
                self._tools = None
                self._last_error = None


class McpRuntimeManager:
    """Process-level singleton managing all MCP scopes.

    Thread-safe for concurrent domain agent execution.
    """

    def __init__(self):
        self._scopes: dict[str, _ScopedMCPClient] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Scope lifecycle
    # ------------------------------------------------------------------

    async def load_scope(self, scope_key: str, servers: dict[str, McpServerConfig]) -> bool:
        """Load (or reuse) MCP connections for a scope.

        Args:
            scope_key: Scope identifier (e.g. ``"global"``, ``"domain:contacts-agent"``).
            servers: Map of server name → config.

        Returns:
            ``True`` if all servers connected successfully.
        """
        async with self._lock:
            if scope_key not in self._scopes:
                self._scopes[scope_key] = _ScopedMCPClient(scope_key, servers)

        return await self._scopes[scope_key].connect()

    async def get_tools(self, scope_key: str) -> list[BaseTool]:
        """Return tools for a scope. Returns empty list if scope not loaded."""
        client = self._scopes.get(scope_key)
        if client is None:
            return []
        return await client.get_tools()

    def get_tools_sync(self, scope_key: str) -> list[BaseTool]:
        """Return cached tools synchronously (for LangGraph checkpointing safety)."""
        client = self._scopes.get(scope_key)
        if client is None:
            return []
        return client.get_tools_sync()

    def get_scope_error(self, scope_key: str) -> str | None:
        client = self._scopes.get(scope_key)
        return client.last_error if client else None

    def is_scope_loaded(self, scope_key: str) -> bool:
        return scope_key in self._scopes

    async def unload_scope(self, scope_key: str) -> None:
        async with self._lock:
            client = self._scopes.pop(scope_key, None)
        if client:
            await client.disconnect()

    async def shutdown(self) -> None:
        """Disconnect all scopes (call on app shutdown)."""
        for key, client in list(self._scopes.items()):
            await client.disconnect()
            logger.info("[McpRuntime] Disconnected scope '%s'.", key)
        self._scopes.clear()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @staticmethod
    def scope_key_for_agent(agent_name: str, tenant_id: str | None = None) -> str:
        if tenant_id and tenant_id != "default":
            return f"tenant:{tenant_id}:domain:{agent_name}"
        return f"domain:{agent_name}"

    @staticmethod
    def scope_key_for_run(run_id: str) -> str:
        return f"run:{run_id}"

    @staticmethod
    def scope_key_for_tenant(tenant_id: str | None = None) -> str:
        """Global scope key, optionally tenant-partitioned."""
        if tenant_id and tenant_id != "default":
            return f"tenant:{tenant_id}:global"
        return "global"



# Process-level singleton
mcp_runtime = McpRuntimeManager()


async def _unload_tenant_scopes_async(tenant_id: str) -> int:
    """Disconnect and remove all MCP scopes belonging to a tenant."""
    prefix = f"tenant:{tenant_id}:"
    removed = 0
    async with mcp_runtime._lock:
        keys_to_remove = [k for k in list(mcp_runtime._scopes.keys()) if k.startswith(prefix)]
    for key in keys_to_remove:
        await mcp_runtime.disconnect_scope(key)
        removed += 1
    if removed:
        logger.info("[McpRuntime] Unloaded %d scope(s) for tenant=%s", removed, tenant_id)
    return removed


def unload_tenant_scopes(tenant_id: str) -> int:
    """Synchronous wrapper: disconnect all MCP scopes for a tenant.

    Used by lifecycle operations (tenant decommission).
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _unload_tenant_scopes_async(tenant_id))
                return future.result()
        else:
            return loop.run_until_complete(_unload_tenant_scopes_async(tenant_id))
    except RuntimeError:
        return asyncio.run(_unload_tenant_scopes_async(tenant_id))
