"""OIDC / SSO JWT verification middleware for DeerFlow Gateway.

Dual-role middleware:

1. External resource-server role — verifies OIDC bearer tokens against the
   configured JWKS (original behaviour).
2. Internal SSO role — verifies ``df_session`` cookies that DeerFlow itself
   signed after a successful moss-hub ticket exchange (``kid=df-internal-v1``,
   HS256 with ``DEERFLOW_JWT_SECRET``).

Token source precedence:

- ``Authorization: Bearer <jwt>`` header wins when present.
- Otherwise the cookie named ``SSO_COOKIE_NAME`` (default ``df_session``) is
  read.
- Empty / missing tokens are rejected with ``401`` when auth is enabled.

Verification routing (by JWT header ``kid``):

- ``kid=df-internal-v1`` → local HS256 with ``DEERFLOW_JWT_SECRET``. Must also
  be ``alg=HS256``; any other algorithm is rejected.
- any other ``kid``       → existing JWKS flow (keeps OIDC compatibility).
- missing ``kid``         → rejected (``401 sso_token_invalid``).

Every rejection writes a ``sso_token_invalid`` event to
:class:`src.gateway.sso.audit.AuthAuditLedger` before responding, so
operators can detect replay / brute-force attempts.
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

from src.gateway.sso.audit import extract_client_ip, get_default_ledger
from src.gateway.sso.config import SSOConfig
from src.gateway.sso.jwt_signer import INTERNAL_KID, verify_df_session

logger = logging.getLogger(__name__)


# ── JWKS cache ──────────────────────────────────────────────────────────


class _JWKSCache:
    """Thread-safe JWKS public-key cache with TTL-based expiry."""

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
        with httpx.Client(verify=self._verify_ssl, timeout=10) as client:
            resp = client.get(self._jwks_uri)
            resp.raise_for_status()
            return resp.json().get("keys", [])

    def get_keys(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        if not force_refresh and self._keys and not self._is_expired:
            return self._keys
        with self._lock:
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
        for key in self.get_keys():
            if key.get("kid") == kid:
                return key
        for key in self.get_keys(force_refresh=True):
            if key.get("kid") == kid:
                return key
        raise JWTError(f"No matching signing key for kid={kid!r}")


# ── Token verification helpers ─────────────────────────────────────────


def _verify_external_token(
    token: str,
    *,
    jwks_cache: _JWKSCache,
    algorithms: list[str],
    audience: str | None,
    issuer: str | None,
) -> dict[str, Any]:
    """Verify an external OIDC JWT against the JWKS cache."""
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


# Back-compat alias for existing tests that imported the pre-rename symbol.
_verify_token = _verify_external_token


def _extract_tenant_id(claims: dict[str, Any], tenant_claims: list[str]) -> str | None:
    """Extract tenant identifier from JWT claims (OIDC path)."""
    for claim_name in tenant_claims:
        value = claims.get(claim_name)
        if value is None:
            continue
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, dict):
            keys = list(value.keys())
            if keys:
                return keys[0]
    return None


# ── Middleware ──────────────────────────────────────────────────────────


class OIDCAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via Bearer OIDC token or ``df_session`` cookie.

    Mount condition (in ``app.py``): either ``OIDC_ENABLED`` or
    ``SSO_ENABLED`` is true. Pass both configs so either auth path is
    available.
    """

    def __init__(self, app: Any, *, config: Any, sso_config: SSOConfig | None = None) -> None:
        super().__init__(app)
        self._config = config
        self._sso_config = sso_config
        self._jwks_cache: _JWKSCache | None = None
        if getattr(config, "enabled", False) and getattr(config, "jwks_uri", ""):
            self._jwks_cache = _JWKSCache(
                config.jwks_uri,
                ttl=config.jwks_cache_ttl,
                verify_ssl=config.verify_ssl,
            )
        self._exempt_paths: set[str] = set(getattr(config, "exempt_paths", set()) or set())
        if sso_config is not None:
            self._exempt_paths |= set(sso_config.exempt_paths)
        self._exempt_prefixes: list[str] = list(
            getattr(config, "exempt_path_prefixes", []) or []
        )

    def _is_exempt(self, path: str) -> bool:
        if path in self._exempt_paths:
            return True
        for prefix in self._exempt_prefixes:
            if path.startswith(prefix):
                return True
        return False

    def _read_token(self, request: Request) -> tuple[str | None, str | None]:
        """Return ``(token, source)`` where source is ``"bearer"`` or ``"cookie"``."""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            if token:
                return token, "bearer"
            return "", "bearer"
        if self._sso_config is not None and self._sso_config.enabled:
            cookie = request.cookies.get(self._sso_config.cookie_name)
            if cookie:
                return cookie.strip(), "cookie"
        return None, None

    def _audit_token_invalid(self, request: Request, reason: str, kid: str | None = None) -> None:
        try:
            client_ip = extract_client_ip(
                request.headers,
                request.client.host if request.client else None,
            )
            get_default_ledger().record_token_invalid(
                reason=reason,
                client_ip=client_ip,
                user_agent=request.headers.get("user-agent"),
                kid=kid,
            )
        except Exception:  # pragma: no cover — never let audit kill a request
            logger.exception("Failed to record sso_token_invalid audit event")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if self._is_exempt(path):
            return await call_next(request)

        token, source = self._read_token(request)
        if token is None:
            self._audit_token_invalid(request, "missing_token")
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )
        if not token:
            self._audit_token_invalid(request, f"empty_{source}_token")
            return JSONResponse(status_code=401, content={"detail": "Empty token"})

        # Peek at the header to route between internal HS256 and external JWKS.
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            self._audit_token_invalid(request, f"malformed_header: {exc}")
            return JSONResponse(status_code=401, content={"detail": f"Invalid token: {exc}"})
        kid = header.get("kid")
        alg = header.get("alg")

        if not kid:
            self._audit_token_invalid(request, "missing_kid")
            return JSONResponse(status_code=401, content={"detail": "Token missing 'kid'"})

        claims: dict[str, Any]
        try:
            if kid == INTERNAL_KID:
                if self._sso_config is None or not self._sso_config.enabled:
                    self._audit_token_invalid(request, "internal_token_but_sso_disabled", kid=kid)
                    return JSONResponse(status_code=401, content={"detail": "Invalid token"})
                if alg != "HS256":
                    self._audit_token_invalid(request, f"internal_token_bad_alg:{alg}", kid=kid)
                    return JSONResponse(status_code=401, content={"detail": "Invalid token"})
                claims = verify_df_session(token, config=self._sso_config)
            else:
                if self._jwks_cache is None:
                    self._audit_token_invalid(request, "external_token_but_oidc_disabled", kid=kid)
                    return JSONResponse(status_code=401, content={"detail": "Invalid token"})
                claims = _verify_external_token(
                    token,
                    jwks_cache=self._jwks_cache,
                    algorithms=self._config.algorithms,
                    audience=self._config.audience,
                    issuer=self._config.issuer,
                )
        except JWTError as exc:
            self._audit_token_invalid(request, f"verification_failed: {exc}", kid=kid)
            logger.warning("JWT verification failed: %s", exc)
            return JSONResponse(status_code=401, content={"detail": f"Invalid token: {exc}"})
        except httpx.HTTPError as exc:
            logger.error("JWKS fetch failed during token verification: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication service unavailable"},
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("Unexpected error during token verification")
            return JSONResponse(status_code=500, content={"detail": f"Authentication error: {exc}"})

        # Inject identity context.
        request.state.claims = claims
        request.state.user_id = claims.get("sub") or None
        request.state.username = claims.get("preferred_username", "") or ""
        request.state.role = claims.get("role") or None
        request.state.employee_no = claims.get("employee_no") or None
        request.state.target_system = claims.get("target_system") or None
        request.state.token_source = source
        request.state.token_kid = kid

        if kid == INTERNAL_KID:
            # Internal tokens already carry the moss-hub tenant id; fall back
            # to the configured tenant when the claim is unexpectedly missing.
            request.state.tenant_id = (
                claims.get("tenant_id")
                or (self._sso_config.tenant_id if self._sso_config else None)
            )
        else:
            request.state.tenant_id = _extract_tenant_id(claims, self._config.tenant_claims)

        return await call_next(request)
