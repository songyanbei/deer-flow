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
_ANONYMOUS_USER = "anonymous"


@dataclass
class _TenantCache:
    """Per-tenant (or per-user) MCP tools cache entry."""
    tools: list[BaseTool] | None = None
    initialized: bool = False
    config_mtime: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Keyed by composite cache key (e.g. "default:anonymous", "acme:user123")
_tenant_caches: dict[str, _TenantCache] = {}
_global_lock = asyncio.Lock()


def _cache_key(tenant_id: str | None, user_id: str | None = None) -> str:
    """Compute composite cache key.

    When a user has no personal extensions_config, we fall back to the
    tenant-level key to avoid creating redundant cache entries.
    """
    tid = tenant_id or _DEFAULT_TENANT
    uid = user_id or _ANONYMOUS_USER
    if uid != _ANONYMOUS_USER and tid != _DEFAULT_TENANT:
        try:
            from src.config.paths import get_paths
            user_cfg = get_paths().tenant_user_extensions_config(tid, uid)
            if user_cfg.exists():
                return f"{tid}:{uid}"
        except Exception:
            pass
    return f"{tid}:{_ANONYMOUS_USER}"


def _get_tenant_cache(tenant_id: str | None, user_id: str | None = None) -> _TenantCache:
    key = _cache_key(tenant_id, user_id)
    return _tenant_caches.setdefault(key, _TenantCache())


def _get_config_mtime(tenant_id: str | None = None, user_id: str | None = None) -> float | None:
    """Get the modification time of the extensions config file.

    For non-default tenants, also checks the tenant overlay file.
    For non-anonymous users, also checks the user overlay file.
    Returns the most recent mtime across all layers.
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

    # User overlay mtime
    if user_id and user_id != _ANONYMOUS_USER and tenant_id and tenant_id != _DEFAULT_TENANT:
        try:
            from src.config.paths import get_paths
            user_cfg = get_paths().tenant_user_extensions_config(tenant_id, user_id)
            if user_cfg.exists():
                u_mtime = os.path.getmtime(user_cfg)
                mtime = max(mtime or 0, u_mtime)
        except Exception:
            pass

    return mtime


def _is_cache_stale(tc: _TenantCache, tenant_id: str | None, user_id: str | None = None) -> bool:
    if not tc.initialized:
        return False

    current_mtime = _get_config_mtime(tenant_id, user_id)
    if tc.config_mtime is None or current_mtime is None:
        return False

    if current_mtime > tc.config_mtime:
        logger.info("MCP config modified for tenant=%s user=%s (mtime: %s -> %s), cache stale", tenant_id or _DEFAULT_TENANT, user_id or _ANONYMOUS_USER, tc.config_mtime, current_mtime)
        return True

    return False


async def initialize_mcp_tools(tenant_id: str | None = None, user_id: str | None = None) -> list[BaseTool]:
    """Initialize and cache MCP tools for a tenant/user partition."""
    tc = _get_tenant_cache(tenant_id, user_id)

    async with tc.lock:
        if tc.initialized:
            return tc.tools or []

        from src.mcp.tools import get_mcp_tools

        key = _cache_key(tenant_id, user_id)
        logger.info("Initializing MCP tools for %s ...", key)
        tc.tools = await get_mcp_tools(tenant_id=tenant_id, user_id=user_id)
        tc.initialized = True
        tc.config_mtime = _get_config_mtime(tenant_id, user_id)
        logger.info("MCP tools initialized for %s: %d tool(s) (mtime: %s)", key, len(tc.tools), tc.config_mtime)
        return tc.tools


def get_cached_mcp_tools(tenant_id: str | None = None, user_id: str | None = None) -> list[BaseTool]:
    """Get cached MCP tools with lazy initialization.

    Checks for config staleness and re-initializes when needed.
    Partitioned by ``(tenant_id, user_id)`` composite key.
    """
    tc = _get_tenant_cache(tenant_id, user_id)
    key = _cache_key(tenant_id, user_id)

    if _is_cache_stale(tc, tenant_id, user_id):
        logger.info("MCP cache stale for %s, resetting ...", key)
        _reset_tenant_cache(tc)

    if not tc.initialized:
        logger.info("MCP tools not initialized for %s, lazy init ...", key)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, initialize_mcp_tools(tenant_id, user_id))
                    future.result()
            else:
                loop.run_until_complete(initialize_mcp_tools(tenant_id, user_id))
        except RuntimeError:
            asyncio.run(initialize_mcp_tools(tenant_id, user_id))
        except Exception as e:
            logger.error("Failed to lazy-initialize MCP tools for %s: %s", key, e)
            return []

    return tc.tools or []


def _reset_tenant_cache(tc: _TenantCache) -> None:
    tc.tools = None
    tc.initialized = False
    tc.config_mtime = None


def reset_mcp_tools_cache(tenant_id: str | None = None, user_id: str | None = None) -> None:
    """Reset the MCP tools cache for a specific tenant/user (or all if None)."""
    if tenant_id is not None:
        key = _cache_key(tenant_id, user_id)
        tc = _tenant_caches.get(key)
        if tc:
            _reset_tenant_cache(tc)
            logger.info("MCP tools cache reset for %s", key)
    else:
        for k, tc in _tenant_caches.items():
            _reset_tenant_cache(tc)
        logger.info("MCP tools cache reset for all partitions")


def invalidate_tenant(tenant_id: str) -> None:
    """Remove all cache entries for a specific tenant entirely.

    Used by lifecycle operations (tenant decommission) to free memory.
    Cache keys are composite ``"{tenant_id}:{user_id}"`` strings, so we
    scan for all keys with the matching tenant prefix.
    """
    prefix = f"{tenant_id}:"
    keys_to_remove = [k for k in list(_tenant_caches.keys()) if k.startswith(prefix)]
    for k in keys_to_remove:
        tc = _tenant_caches.pop(k, None)
        if tc:
            _reset_tenant_cache(tc)
    if keys_to_remove:
        logger.info("MCP cache invalidated and removed %d partition(s) for tenant=%s", len(keys_to_remove), tenant_id)
