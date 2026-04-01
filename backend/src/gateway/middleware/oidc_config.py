"""OIDC configuration for DeerFlow Gateway.

All OIDC settings are loaded from environment variables with sensible defaults.
When ``OIDC_ENABLED`` is ``false`` (default), the middleware is not mounted
and the gateway runs without authentication — matching the current dev behaviour.

Environment variables
---------------------
OIDC_ENABLED
    Master switch. Set to ``true`` to enable OIDC authentication.
OIDC_ISSUER
    Token issuer URL (``iss`` claim validation). Typically the Keycloak realm URL.
OIDC_JWKS_URI
    URL to fetch JWKS public keys. If empty, auto-discovered from
    ``{OIDC_ISSUER}/protocol/openid-connect/certs`` (Keycloak convention).
OIDC_AUDIENCE
    Expected ``aud`` claim value (Keycloak client ID). Leave empty to skip
    audience validation.
OIDC_ALGORITHMS
    Comma-separated list of accepted JWT signing algorithms.
OIDC_VERIFY_SSL
    Whether to verify TLS certificates when fetching JWKS. Set to ``false``
    for internal deployments with self-signed certs.
OIDC_TENANT_CLAIMS
    Comma-separated, priority-ordered list of JWT claim names to extract
    tenant identifier from. The first non-empty match wins.
OIDC_JWKS_CACHE_TTL
    JWKS cache time-to-live in seconds.
OIDC_EXEMPT_PATHS
    Comma-separated list of exact paths that skip authentication.
OIDC_EXEMPT_PATH_PREFIXES
    Comma-separated list of path prefixes that skip authentication.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    if raw.strip().isdigit():
        return int(raw)
    return default


def _env_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Default paths that never require authentication.
_DEFAULT_EXEMPT_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/debug/metrics",
}

# Default path prefixes that never require authentication.
_DEFAULT_EXEMPT_PATH_PREFIXES: list[str] = [
    "/docs",
    "/redoc",
]


@dataclass(frozen=True)
class OIDCConfig:
    """Immutable OIDC configuration, loaded once at startup."""

    enabled: bool = False
    issuer: str | None = None
    jwks_uri: str = ""
    audience: str | None = None
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    verify_ssl: bool = True
    tenant_claims: list[str] = field(default_factory=lambda: ["organization", "tenant_id", "org_id"])
    jwks_cache_ttl: int = 3600
    exempt_paths: set[str] = field(default_factory=lambda: set(_DEFAULT_EXEMPT_PATHS))
    exempt_path_prefixes: list[str] = field(default_factory=lambda: list(_DEFAULT_EXEMPT_PATH_PREFIXES))


def load_oidc_config() -> OIDCConfig:
    """Load OIDC configuration from environment variables.

    Returns a frozen ``OIDCConfig`` instance. Call this once during application
    startup (in the lifespan handler) and pass the result to the middleware.
    """
    enabled = _env_bool("OIDC_ENABLED", False)

    issuer = os.getenv("OIDC_ISSUER", "").strip() or None

    jwks_uri = os.getenv("OIDC_JWKS_URI", "").strip()
    if not jwks_uri and issuer:
        # Auto-discover JWKS URI from Keycloak-style issuer URL.
        jwks_uri = f"{issuer.rstrip('/')}/protocol/openid-connect/certs"

    audience = os.getenv("OIDC_AUDIENCE", "").strip() or None

    algorithms = _env_list("OIDC_ALGORITHMS", "RS256")
    verify_ssl = _env_bool("OIDC_VERIFY_SSL", True)
    tenant_claims = _env_list("OIDC_TENANT_CLAIMS", "organization,tenant_id,org_id")
    jwks_cache_ttl = _env_int("OIDC_JWKS_CACHE_TTL", 3600)

    # Merge user-defined exempt paths with defaults.
    user_exempt = _env_list("OIDC_EXEMPT_PATHS", "")
    exempt_paths = set(_DEFAULT_EXEMPT_PATHS) | set(user_exempt)

    user_exempt_prefixes = _env_list("OIDC_EXEMPT_PATH_PREFIXES", "")
    exempt_path_prefixes = list(_DEFAULT_EXEMPT_PATH_PREFIXES) + user_exempt_prefixes

    return OIDCConfig(
        enabled=enabled,
        issuer=issuer,
        jwks_uri=jwks_uri,
        audience=audience,
        algorithms=algorithms,
        verify_ssl=verify_ssl,
        tenant_claims=tenant_claims,
        jwks_cache_ttl=jwks_cache_ttl,
        exempt_paths=exempt_paths,
        exempt_path_prefixes=exempt_path_prefixes,
    )
