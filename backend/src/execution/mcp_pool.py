"""Per-domain-agent MCP connection pool.

Manages stdio MCP server connections on a per-agent basis using
langchain-mcp-adapters' MultiServerMCPClient (same library DeerFlow already uses
for global MCP tools in src/mcp/).

Ported and adapted from laifu-agent-core/execution/mcp_stdio_client.py.

Usage (from executor.py):
    from src.execution.mcp_pool import mcp_pool
    await mcp_pool.init_agent_connections(agent_name, servers_config_list)
    tools = await mcp_pool.get_agent_tools(agent_name)
    await mcp_pool.shutdown()  # on app shutdown
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class _AgentMCPClient:
    """Manages MCP connections for a single domain agent."""

    def __init__(self, agent_name: str, servers: list[dict[str, Any]]):
        self.agent_name = agent_name
        self._servers = servers
        self._tools: list | None = None
        self._client = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def connect(self) -> bool:
        """Establish connections to all configured MCP servers for this agent."""
        async with self._lock:
            if self._tools is not None and self._last_error is None:
                return True

            self._last_error = None

            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
            except ImportError:
                self._last_error = "langchain-mcp-adapters not installed"
                logger.error("[MCPPool] %s. Run: uv add langchain-mcp-adapters", self._last_error)
                self._tools = None
                return False

            # Build server params in the format MultiServerMCPClient expects
            server_params: dict[str, dict[str, Any]] = {}
            for srv in self._servers:
                name = srv.get("name", "")
                if not name:
                    continue
                params: dict[str, Any] = {"transport": "stdio"}
                if cmd := srv.get("command"):
                    params["command"] = cmd
                if args := srv.get("args"):
                    params["args"] = args
                if env := srv.get("env"):
                    params["env"] = env
                server_params[name] = params

            if not server_params:
                if self._servers:
                    self._last_error = "no valid MCP server configs"
                    logger.warning("[MCPPool] Agent '%s': %s.", self.agent_name, self._last_error)
                    self._tools = None
                    return False
                self._tools = []
                return True

            try:
                self._client = MultiServerMCPClient(server_params)
                self._tools = await self._client.get_tools()
                self._last_error = None
                logger.info("[MCPPool] Agent '%s': connected %d server(s), loaded %d tool(s).", self.agent_name, len(server_params), len(self._tools))
                return True
            except Exception as e:
                self._last_error = str(e)
                logger.error("[MCPPool] Agent '%s': connection failed: %s", self.agent_name, e)
                self._tools = None
                return False

    async def get_tools(self) -> list:
        """Return cached tool list (must call connect() first)."""
        if self._tools is None:
            success = await self.connect()
            if not success:
                return []
        return self._tools or []

    async def disconnect(self) -> None:
        """Close connections gracefully."""
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.__aexit__(None, None, None), timeout=5.0)
            except Exception as e:
                logger.warning("[MCPPool] Agent '%s': disconnect warning: %s", self.agent_name, e)
            finally:
                self._client = None
                self._tools = None
                self._last_error = None


class MCPPool:
    """Process-level singleton that manages per-agent MCP connections.

    Thread-safe for concurrent domain agent execution (Phase 2 parallel).
    """

    def __init__(self):
        self._agents: dict[str, _AgentMCPClient] = {}
        self._lock = asyncio.Lock()

    async def init_agent_connections(self, agent_name: str, servers: list[dict[str, Any]]) -> bool:
        """Initialise (or re-use) MCP connections for a domain agent.

        Args:
            agent_name: Domain agent name (must match AgentConfig.name).
            servers: List of server config dicts with keys: name, command, args, env.
        """
        async with self._lock:
            if agent_name not in self._agents:
                self._agents[agent_name] = _AgentMCPClient(agent_name, servers)

        return await self._agents[agent_name].connect()

    async def get_agent_tools(self, agent_name: str) -> list:
        """Return the cached LangChain tools for a domain agent.

        Returns an empty list if the agent has no MCP connections or init failed.
        """
        client = self._agents.get(agent_name)
        if client is None:
            return []
        return await client.get_tools()

    def get_agent_tools_sync(self, agent_name: str) -> list:
        """Return cached tools synchronously (must call init_agent_connections first).

        This avoids passing StructuredTool objects through RunnableConfig, which would
        cause serialization failures during LangGraph checkpointing (StructuredTool
        contains unpicklable local closures created by langchain-mcp-adapters).
        """
        client = self._agents.get(agent_name)
        if client is None:
            return []
        return client._tools or []

    def get_agent_error(self, agent_name: str) -> str | None:
        """Return the latest MCP connection error for an agent, if any."""
        client = self._agents.get(agent_name)
        if client is None:
            return None
        return client.last_error

    async def shutdown(self) -> None:
        """Disconnect all agents' MCP servers (call on app shutdown)."""
        for name, client in list(self._agents.items()):
            await client.disconnect()
            logger.info("[MCPPool] Disconnected agent '%s'.", name)
        self._agents.clear()


# Process-level singleton — imported by executor.py
mcp_pool = MCPPool()
