"""SSO configuration for DeerFlow gateway.

Loaded once at application startup. When ``SSO_ENABLED=false`` (default),
the SSO callback router is still mounted but returns ``503`` until enabled;
the callback is only meaningful when the required moss-hub credentials and
``DEERFLOW_JWT_SECRET`` are present.

Environment variables
---------------------
SSO_ENABLED
    Master switch. ``true`` to accept moss-hub tickets and mint ``df_session``.
MOSS_HUB_BASE_URL
    Base URL of the moss-hub server. Verify-ticket is called at
    ``{base}/api/open/sso/luliu/verify-ticket``.
MOSS_HUB_APP_KEY / MOSS_HUB_APP_SECRET
    S2S credentials. ``APP_SECRET`` must be at least 32 bytes.
MOSS_HUB_VERIFY_SSL
    Whether to verify TLS when calling moss-hub. Default ``true``.
MOSS_HUB_TENANT_ID
    Constant tenant id assigned to moss-hub users. Default ``moss-hub``.
DEERFLOW_JWT_SECRET
    HS256 signing secret for ``df_session``. Must be at least 32 bytes.
SSO_JWT_TTL
    ``df_session`` TTL in seconds. Default ``28800`` (8 hours).
SSO_COOKIE_NAME
    Cookie name. Default ``df_session``.
SSO_COOKIE_DOMAIN
    Cookie ``Domain`` attribute. Empty string means host-only cookie.
SSO_COOKIE_SECURE
    Whether ``Secure`` flag is set. Default ``true``. Allowed to be ``false``
    only in non-production environments — production with ``false`` is a
    startup fatal error.
ENVIRONMENT
    Deployment environment marker.  Values ``production`` and ``prod`` are
    both treated as production for the purpose of enforcing
    ``SSO_COOKIE_SECURE=true``.  Anything else (``staging``/``dev``/``test``/
    empty) is non-production.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    if raw.strip().lstrip("-").isdigit():
        return int(raw)
    return default


_DEFAULT_TENANT_ID = "moss-hub"
_DEFAULT_COOKIE_NAME = "df_session"
_DEFAULT_JWT_TTL = 28800
_MIN_SECRET_BYTES = 32
_PROD_ENVIRONMENTS = frozenset({"production", "prod"})


class SSOConfigError(RuntimeError):
    """Raised on SSO configuration errors (fail-fast at startup)."""


@dataclass(frozen=True)
class SSOConfig:
    """Immutable SSO configuration, loaded once at startup."""

    enabled: bool = False
    moss_hub_base_url: str = ""
    moss_hub_app_key: str = ""
    moss_hub_app_secret: str = ""
    moss_hub_verify_ssl: bool = True
    tenant_id: str = _DEFAULT_TENANT_ID
    jwt_secret: str = ""
    jwt_ttl: int = _DEFAULT_JWT_TTL
    cookie_name: str = _DEFAULT_COOKIE_NAME
    cookie_domain: str = ""
    cookie_secure: bool = True
    environment: str = "dev"
    # Paths exempt from the auth middleware when SSO is enabled.
    # The callback path itself must always be accessible without auth.
    exempt_paths: frozenset[str] = field(
        default_factory=lambda: frozenset({"/api/sso/callback"})
    )


def load_sso_config() -> SSOConfig:
    """Load and validate SSO configuration from environment variables.

    Fail-fast semantics:

    - When ``SSO_ENABLED=true``, all of ``MOSS_HUB_BASE_URL``,
      ``MOSS_HUB_APP_KEY``, ``MOSS_HUB_APP_SECRET``, ``DEERFLOW_JWT_SECRET``
      must be present and non-empty.
    - ``MOSS_HUB_APP_SECRET`` and ``DEERFLOW_JWT_SECRET`` must be at least
      32 bytes (UTF-8 encoded).
    - ``SSO_COOKIE_SECURE=false`` in ``ENVIRONMENT=production`` is rejected.
    """
    enabled = _env_bool("SSO_ENABLED", False)
    moss_hub_base_url = os.getenv("MOSS_HUB_BASE_URL", "").strip()
    moss_hub_app_key = os.getenv("MOSS_HUB_APP_KEY", "").strip()
    moss_hub_app_secret = os.getenv("MOSS_HUB_APP_SECRET", "")
    moss_hub_verify_ssl = _env_bool("MOSS_HUB_VERIFY_SSL", True)
    tenant_id = os.getenv("MOSS_HUB_TENANT_ID", _DEFAULT_TENANT_ID).strip() or _DEFAULT_TENANT_ID
    jwt_secret = os.getenv("DEERFLOW_JWT_SECRET", "")
    jwt_ttl = _env_int("SSO_JWT_TTL", _DEFAULT_JWT_TTL)
    cookie_name = os.getenv("SSO_COOKIE_NAME", _DEFAULT_COOKIE_NAME).strip() or _DEFAULT_COOKIE_NAME
    cookie_domain = os.getenv("SSO_COOKIE_DOMAIN", "").strip()
    cookie_secure = _env_bool("SSO_COOKIE_SECURE", True)
    environment = os.getenv("ENVIRONMENT", "dev").strip().lower() or "dev"

    if enabled:
        missing = [
            name
            for name, value in (
                ("MOSS_HUB_BASE_URL", moss_hub_base_url),
                ("MOSS_HUB_APP_KEY", moss_hub_app_key),
                ("MOSS_HUB_APP_SECRET", moss_hub_app_secret),
                ("DEERFLOW_JWT_SECRET", jwt_secret),
            )
            if not value
        ]
        if missing:
            raise SSOConfigError(
                "SSO_ENABLED=true but required config missing: " + ", ".join(missing)
            )
        if len(moss_hub_app_secret.encode("utf-8")) < _MIN_SECRET_BYTES:
            raise SSOConfigError(
                f"MOSS_HUB_APP_SECRET must be at least {_MIN_SECRET_BYTES} bytes"
            )
        if len(jwt_secret.encode("utf-8")) < _MIN_SECRET_BYTES:
            raise SSOConfigError(
                f"DEERFLOW_JWT_SECRET must be at least {_MIN_SECRET_BYTES} bytes"
            )
        if jwt_ttl <= 0:
            raise SSOConfigError("SSO_JWT_TTL must be a positive integer")
        if not cookie_secure and environment in _PROD_ENVIRONMENTS:
            raise SSOConfigError(
                "SSO_COOKIE_SECURE=false is not allowed in production"
            )

    return SSOConfig(
        enabled=enabled,
        moss_hub_base_url=moss_hub_base_url.rstrip("/"),
        moss_hub_app_key=moss_hub_app_key,
        moss_hub_app_secret=moss_hub_app_secret,
        moss_hub_verify_ssl=moss_hub_verify_ssl,
        tenant_id=tenant_id,
        jwt_secret=jwt_secret,
        jwt_ttl=jwt_ttl,
        cookie_name=cookie_name,
        cookie_domain=cookie_domain,
        cookie_secure=cookie_secure,
        environment=environment,
    )


_cached: SSOConfig | None = None


def get_sso_config() -> SSOConfig:
    """Return a process-wide cached ``SSOConfig``."""
    global _cached
    if _cached is None:
        _cached = load_sso_config()
    return _cached


def reset_sso_config_cache() -> None:
    """Reset the module-level cache (intended for tests)."""
    global _cached
    _cached = None
