"""Health check utilities for SSE/HTTP MCP servers."""

import logging
from urllib.parse import urlparse

import httpx

from src.config.extensions_config import McpServerConfig

logger = logging.getLogger(__name__)

# Default health check path when not explicitly configured
_DEFAULT_HEALTHCHECK_PATH = "/health"


def _extract_origin(url: str) -> str:
    """Extract the origin (scheme + host + port) from a URL.

    For example, ``http://localhost:3001/sse`` → ``http://localhost:3001``.
    This ensures health check paths are appended to the server root,
    not to the SSE/HTTP endpoint path.
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin


async def check_server_health(
    server_name: str,
    config: McpServerConfig,
    timeout: float | None = None,
) -> bool:
    """Probe the health endpoint of an SSE/HTTP MCP server.

    For ``stdio`` servers this is a no-op that always returns ``True``.

    The health URL is built from the **origin** of ``config.url`` (not the
    full SSE/HTTP endpoint path) plus ``config.healthcheck_path``.  For
    example, if ``url`` is ``http://localhost:3001/sse`` and
    ``healthcheck_path`` is ``/health``, the probe hits
    ``http://localhost:3001/health``.

    Args:
        server_name: Human-readable server name (for logging).
        config: The MCP server configuration.
        timeout: Optional override for the HTTP request timeout (seconds).
                 Falls back to ``config.connect_timeout_seconds``.

    Returns:
        ``True`` if the server is healthy or is a stdio server, ``False`` otherwise.
    """
    if config.type == "stdio":
        return True

    if not config.url:
        logger.warning("[McpHealth] Server '%s': no URL configured, skipping health check.", server_name)
        return False

    healthcheck_path = config.healthcheck_path or _DEFAULT_HEALTHCHECK_PATH
    if not healthcheck_path.startswith("/"):
        healthcheck_path = "/" + healthcheck_path
    # Use origin (scheme+host+port) so that /sse or /message paths don't pollute the health URL
    origin = _extract_origin(config.url)
    health_url = origin + healthcheck_path

    effective_timeout = timeout if timeout is not None else config.connect_timeout_seconds

    try:
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.get(health_url, headers=config.headers or {})
            if resp.status_code < 400:
                logger.info("[McpHealth] Server '%s' healthy (%s → %d).", server_name, health_url, resp.status_code)
                return True
            else:
                logger.warning("[McpHealth] Server '%s' unhealthy (%s → %d).", server_name, health_url, resp.status_code)
                return False
    except httpx.TimeoutException:
        logger.warning("[McpHealth] Server '%s' health check timed out (%s, timeout=%.1fs).", server_name, health_url, effective_timeout)
        return False
    except Exception as e:
        logger.warning("[McpHealth] Server '%s' health check failed (%s): %s", server_name, health_url, e)
        return False
