"""Cache for MCP tools to avoid repeated loading.

The cache is partitioned by tenant: each ``tenant_id`` key gets its own
independent tool list and mtime tracker so that tenant-scoped
``extensions_config.json`` changes only invalidate the affected partition.

The default (platform-level) partition uses ``tenant_id="default"``.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_DEFAULT_TENANT = "default"


@dataclass
class _TenantCache:
    """Per-tenant MCP tools cache entry."""
    tools: list[BaseTool] | None = None
    initialized: bool = False
    config_mtime: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Keyed by tenant_id
_tenant_caches: dict[str, _TenantCache] = {}
_global_lock = asyncio.Lock()


def _get_tenant_cache(tenant_id: str | None) -> _TenantCache:
    tid = tenant_id or _DEFAULT_TENANT
    return _tenant_caches.setdefault(tid, _TenantCache())


def _get_config_mtime(tenant_id: str | None = None) -> float | None:
    """Get the modification time of the extensions config file.

    For non-default tenants, also checks the tenant overlay file.
    """
    from src.config.extensions_config import ExtensionsConfig

    mtime: float | None = None

    # Platform-level mtime
    config_path = ExtensionsConfig.resolve_config_path()
    if config_path and config_path.exists():
        mtime = os.path.getmtime(config_path)

    # Tenant overlay mtime — use the most recent of the two
    if tenant_id and tenant_id != _DEFAULT_TENANT:
        try:
            from src.config.paths import get_paths
            tenant_cfg = get_paths().tenant_dir(tenant_id) / "extensions_config.json"
            if tenant_cfg.exists():
                t_mtime = os.path.getmtime(tenant_cfg)
                mtime = max(mtime or 0, t_mtime)
        except Exception:
            pass

    return mtime


def _is_cache_stale(tc: _TenantCache, tenant_id: str | None) -> bool:
    if not tc.initialized:
        return False

    current_mtime = _get_config_mtime(tenant_id)
    if tc.config_mtime is None or current_mtime is None:
        return False

    if current_mtime > tc.config_mtime:
        logger.info("MCP config modified for tenant=%s (mtime: %s -> %s), cache stale", tenant_id or _DEFAULT_TENANT, tc.config_mtime, current_mtime)
        return True

    return False


async def initialize_mcp_tools(tenant_id: str | None = None) -> list[BaseTool]:
    """Initialize and cache MCP tools for a tenant partition."""
    tc = _get_tenant_cache(tenant_id)

    async with tc.lock:
        if tc.initialized:
            return tc.tools or []

        from src.mcp.tools import get_mcp_tools

        tid = tenant_id or _DEFAULT_TENANT
        logger.info("Initializing MCP tools for tenant=%s ...", tid)
        tc.tools = await get_mcp_tools(tenant_id=tenant_id)
        tc.initialized = True
        tc.config_mtime = _get_config_mtime(tenant_id)
        logger.info("MCP tools initialized for tenant=%s: %d tool(s) (mtime: %s)", tid, len(tc.tools), tc.config_mtime)
        return tc.tools


def get_cached_mcp_tools(tenant_id: str | None = None) -> list[BaseTool]:
    """Get cached MCP tools with lazy initialization.

    Checks for config staleness and re-initializes when needed.
    Partitioned by ``tenant_id`` — each tenant sees its own tool set.
    """
    tc = _get_tenant_cache(tenant_id)

    if _is_cache_stale(tc, tenant_id):
        logger.info("MCP cache stale for tenant=%s, resetting ...", tenant_id or _DEFAULT_TENANT)
        _reset_tenant_cache(tc)

    if not tc.initialized:
        logger.info("MCP tools not initialized for tenant=%s, lazy init ...", tenant_id or _DEFAULT_TENANT)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, initialize_mcp_tools(tenant_id))
                    future.result()
            else:
                loop.run_until_complete(initialize_mcp_tools(tenant_id))
        except RuntimeError:
            asyncio.run(initialize_mcp_tools(tenant_id))
        except Exception as e:
            logger.error("Failed to lazy-initialize MCP tools for tenant=%s: %s", tenant_id or _DEFAULT_TENANT, e)
            return []

    return tc.tools or []


def _reset_tenant_cache(tc: _TenantCache) -> None:
    tc.tools = None
    tc.initialized = False
    tc.config_mtime = None


def reset_mcp_tools_cache(tenant_id: str | None = None) -> None:
    """Reset the MCP tools cache for a specific tenant (or all if None)."""
    if tenant_id is not None:
        tc = _tenant_caches.get(tenant_id or _DEFAULT_TENANT)
        if tc:
            _reset_tenant_cache(tc)
            logger.info("MCP tools cache reset for tenant=%s", tenant_id or _DEFAULT_TENANT)
    else:
        for tid, tc in _tenant_caches.items():
            _reset_tenant_cache(tc)
        logger.info("MCP tools cache reset for all tenants")


def invalidate_tenant(tenant_id: str) -> None:
    """Remove the cache entry for a specific tenant entirely.

    Used by lifecycle operations (tenant decommission) to free memory.
    """
    tc = _tenant_caches.pop(tenant_id, None)
    if tc:
        _reset_tenant_cache(tc)
        logger.info("MCP cache invalidated and removed for tenant=%s", tenant_id)
