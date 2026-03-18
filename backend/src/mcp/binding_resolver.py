"""Resolve an agent's MCP binding into concrete server configurations.

The resolver translates ``McpBindingConfig`` (declarative names) into a dict
of ``McpServerConfig`` objects ready for the unified runtime manager.

Resolution rules:
  1. ``use_global=True`` → include all ``category='global'`` servers from
     the platform config.
  2. ``domain`` list → look up each name in the platform config.
     If a name is **not** found in the platform config but **is** present
     in the legacy ``mcp_servers`` list (``AgentConfig``), build a synthetic
     ``McpServerConfig`` from the legacy entry so that old YAML configs
     keep working.
  3. ``shared`` list → look up each name; these are expected to exist in
     the platform config with ``category='shared'``.
  4. ``ephemeral`` → reserved, currently a no-op.
"""

import logging
from typing import Any

from src.config.agents_config import AgentConfig, McpBindingConfig, McpServerEntry
from src.config.extensions_config import ExtensionsConfig, McpServerConfig

logger = logging.getLogger(__name__)


def _legacy_entry_to_server_config(entry: McpServerEntry) -> McpServerConfig:
    """Convert a legacy ``McpServerEntry`` (agent YAML) into a ``McpServerConfig``."""
    return McpServerConfig(
        enabled=True,
        type="stdio",
        command=entry.command,
        args=entry.args,
        env=entry.env,
        category="domain",
        domain=entry.name,
    )


def resolve_binding(
    binding: McpBindingConfig,
    extensions_config: ExtensionsConfig,
    agent_config: AgentConfig | None = None,
) -> dict[str, McpServerConfig]:
    """Resolve a binding into a flat dict of server name → config.

    Args:
        binding: The agent's MCP binding declaration.
        extensions_config: Platform-level MCP server registry.
        agent_config: Optional agent config for legacy ``mcp_servers`` fallback.

    Returns:
        Merged dict of server configs ready for runtime manager.
    """
    resolved: dict[str, McpServerConfig] = {}

    # 1. Global servers
    if binding.use_global:
        global_servers = extensions_config.get_servers_by_category("global")
        resolved.update(global_servers)
        if global_servers:
            logger.debug("[BindingResolver] Added %d global server(s).", len(global_servers))

    # Build legacy lookup for fallback
    legacy_lookup: dict[str, McpServerEntry] = {}
    if agent_config:
        legacy_lookup = {s.name: s for s in agent_config.mcp_servers}

    # 2. Domain servers
    for name in binding.domain:
        if name in resolved:
            continue  # already included (e.g. via global)
        platform_cfg = extensions_config.mcp_servers.get(name)
        if platform_cfg and platform_cfg.enabled:
            resolved[name] = platform_cfg
        elif name in legacy_lookup:
            resolved[name] = _legacy_entry_to_server_config(legacy_lookup[name])
            logger.debug("[BindingResolver] Server '%s' resolved from legacy mcp_servers.", name)
        else:
            logger.warning("[BindingResolver] Domain server '%s' not found in platform config or legacy entries.", name)

    # 3. Shared servers
    for name in binding.shared:
        if name in resolved:
            continue
        platform_cfg = extensions_config.mcp_servers.get(name)
        if platform_cfg and platform_cfg.enabled:
            resolved[name] = platform_cfg
        else:
            logger.warning("[BindingResolver] Shared server '%s' not found or disabled in platform config.", name)

    # 4. Ephemeral — reserved, no-op
    if binding.ephemeral:
        logger.debug("[BindingResolver] Ephemeral servers declared but not yet supported: %s", binding.ephemeral)

    return resolved


def resolve_for_main_agent(extensions_config: ExtensionsConfig) -> dict[str, McpServerConfig]:
    """Resolve MCP servers for the main (top-level) agent.

    The main agent gets **only** ``category='global'`` servers by default.
    """
    return extensions_config.get_servers_by_category("global")
