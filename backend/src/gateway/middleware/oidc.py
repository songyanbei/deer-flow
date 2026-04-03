"""OIDC JWT verification middleware for DeerFlow Gateway.

Implements the Resource Server role in OAuth2/OIDC:
- Validates JWT access tokens from the Authorization header
- Fetches and caches JWKS public keys from the OIDC provider
- Injects tenant_id, user_id, and full claims into request.state
- Supports configurable tenant claim extraction
- Handles self-signed certificates (common in internal deployments)

Usage:
    Mounted in gateway app.py via ``app.add_middleware(OIDCAuthMiddleware)``.
    Requires ``OIDCConfig`` to be initialized (see ``oidc_config.py``).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from fastapi import Request
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── JWKS cache ──────────────────────────────────────────────────────────
# Process-level cache with TTL and thread-safe refresh.


class _JWKSCache:
    """Thread-safe JWKS public-key cache with TTL-based expiry.

    Fetches the JWKS key set from the OIDC provider and caches it in memory.
    Automatically refreshes when the TTL expires or when a ``kid`` mismatch
    forces an early invalidation (key rotation scenario).
    """

    def __init__(self, jwks_uri: str, *, ttl: int = 3600, verify_ssl: bool = True) -> None:
        self._jwks_uri = jwks_uri
        self._ttl = ttl
        self._verify_ssl = verify_ssl
        self._keys: list[dict[str, Any]] = []
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def _is_expired(self) -> bool:
        return (time.time() - self._fetched_at) >= self._ttl

    def _fetch(self) -> list[dict[str, Any]]:
        """Synchronous JWKS fetch (called under lock)."""
        with httpx.Client(verify=self._verify_ssl, timeout=10) as client:
            resp = client.get(self._jwks_uri)
            resp.raise_for_status()
            return resp.json().get("keys", [])

    def get_keys(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return cached JWKS keys, refreshing if stale or forced."""
        if not force_refresh and self._keys and not self._is_expired:
            return self._keys

        with self._lock:
            # Double-check after acquiring lock — another thread may have refreshed.
            if not force_refresh and self._keys and not self._is_expired:
                return self._keys
            try:
                self._keys = self._fetch()
                self._fetched_at = time.time()
                logger.info("JWKS refreshed successfully (%d keys)", len(self._keys))
            except httpx.HTTPError as exc:
                logger.error("Failed to refresh JWKS from %s: %s", self._jwks_uri, exc)
                if self._keys:
                    logger.warning("Using stale JWKS keys as fallback")
                else:
                    raise
        return self._keys

    def find_key(self, kid: str) -> dict[str, Any]:
        """Find a key by ``kid``, force-refreshing once on miss (key rotation)."""
        for key in self.get_keys():
            if key.get("kid") == kid:
                return key
        # kid not found — maybe the provider rotated keys. Refresh once.
        for key in self.get_keys(force_refresh=True):
            if key.get("kid") == kid:
                return key
        raise JWTError(f"No matching signing key for kid={kid!r}")


# ── Token verification ──────────────────────────────────────────────────


def _verify_token(
    token: str,
    *,
    jwks_cache: _JWKSCache,
    algorithms: list[str],
    audience: str | None,
    issuer: str | None,
) -> dict[str, Any]:
    """Verify a JWT and return its decoded payload.

    Steps:
    1. Extract ``kid`` from the unverified header.
    2. Look up the matching public key in the JWKS cache.
    3. Decode and verify signature, expiry, audience, and issuer.

    Raises ``JWTError`` on any verification failure.
    """
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if not kid:
        raise JWTError("Token header missing 'kid'")

    signing_key = jwks_cache.find_key(kid)

    return jwt.decode(
        token,
        signing_key,
        algorithms=algorithms,
        audience=audience,
        issuer=issuer,
        options={
            "verify_exp": True,
            "verify_aud": audience is not None,
            "verify_iss": issuer is not None,
        },
    )


# ── Tenant claim extraction ────────────────────────────────────────────


def _extract_tenant_id(claims: dict[str, Any], tenant_claims: list[str]) -> str:
    """Extract tenant identifier from JWT claims.

    Tries each claim name in ``tenant_claims`` in order, returning the first
    non-empty value found. Falls back to ``"default"`` when no tenant claim
    is present (single-tenant deployments).

    Handles both string and list values (Keycloak ``organization`` claim
    can be a list of org names).
    """
    for claim_name in tenant_claims:
        value = claims.get(claim_name)
        if value is None:
            continue
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, dict):
            # Keycloak nested org structure: {"org-name": {"roles": [...]}}
            keys = list(value.keys())
            if keys:
                return keys[0]
    # Return None instead of "default" so that dependencies.py can decide
    # whether to reject (OIDC enabled) or fall back (single-tenant mode).
    return None


# ── Middleware ──────────────────────────────────────────────────────────


class OIDCAuthMiddleware(BaseHTTPMiddleware):
    """OIDC Resource Server middleware.

    Validates the ``Authorization: Bearer <jwt>`` header on every request
    (except exempt paths) and injects identity context into ``request.state``:

    - ``request.state.tenant_id`` — extracted from configurable claim(s)
    - ``request.state.user_id`` — from ``sub`` claim
    - ``request.state.username`` — from ``preferred_username`` claim
    - ``request.state.claims`` — full decoded JWT payload

    Configuration is provided by ``OIDCConfig`` (see ``oidc_config.py``).
    """

    def __init__(self, app: Any, *, config: Any) -> None:
        super().__init__(app)
        self._config = config
        self._jwks_cache = _JWKSCache(
            config.jwks_uri,
            ttl=config.jwks_cache_ttl,
            verify_ssl=config.verify_ssl,
        )

    async def dispatch(self, request: Request, call_next):
        # Skip authentication for exempt paths.
        path = request.url.path.rstrip("/")
        if path in self._config.exempt_paths:
            return await call_next(request)

        # Also skip paths matching exempt prefixes (e.g. /docs, /redoc).
        for prefix in self._config.exempt_path_prefixes:
            if path.startswith(prefix):
                return await call_next(request)

        # Extract Bearer token.
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid Authorization header"})

        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Empty Bearer token"})

        # Verify JWT.
        try:
            claims = _verify_token(
                token,
                jwks_cache=self._jwks_cache,
                algorithms=self._config.algorithms,
                audience=self._config.audience,
                issuer=self._config.issuer,
            )
        except JWTError as exc:
            logger.warning("JWT verification failed: %s", exc)
            return JSONResponse(status_code=401, content={"detail": f"Invalid token: {exc}"})
        except httpx.HTTPError as exc:
            logger.error("JWKS fetch failed during token verification: %s", exc)
            return JSONResponse(status_code=503, content={"detail": "Authentication service unavailable"})
        except Exception as exc:
            logger.exception("Unexpected error during token verification")
            return JSONResponse(status_code=500, content={"detail": f"Authentication error: {exc}"})

        # Inject identity context.
        # tenant_id may be None when the claim is absent — dependencies.py
        # will reject the request (OIDC enabled → missing tenant is an error).
        request.state.tenant_id = _extract_tenant_id(claims, self._config.tenant_claims)
        # Do not fall back to "anonymous": let dependencies.py decide based on
        # whether OIDC is enabled.
        request.state.user_id = claims.get("sub") or None
        request.state.username = claims.get("preferred_username", "")
        request.state.role = claims.get("role") or None
        request.state.claims = claims

        return await call_next(request)
