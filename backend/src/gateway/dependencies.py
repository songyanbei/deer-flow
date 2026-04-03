"""FastAPI dependency helpers for tenant-aware endpoints.

These dependencies extract identity context injected by ``OIDCAuthMiddleware``
and provide it to router endpoints via FastAPI's dependency injection system.

When OIDC is disabled, ``request.state`` has no identity attributes.  In that
case these dependencies fall back to sensible defaults so the application
continues to work in single-tenant / development mode.

When OIDC **is** enabled, missing ``tenant_id`` or ``user_id`` is a hard
error (401) — no silent degradation to ``"default"`` / ``"anonymous"``.
"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request


def _is_oidc_enabled() -> bool:
    """Return True when the OIDC middleware is active.

    This mirrors the same env-var check used by ``oidc_config.py`` at startup.
    """
    return os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes")


def get_tenant_id(request: Request) -> str:
    """Extract ``tenant_id`` from the request (set by OIDCAuthMiddleware).

    * OIDC enabled  → missing / empty tenant is **401 Unauthorized**.
    * OIDC disabled → falls back to ``"default"`` (single-tenant mode).
    """
    raw = getattr(request.state, "tenant_id", None)
    if not raw or not str(raw).strip():
        if _is_oidc_enabled():
            raise HTTPException(status_code=401, detail="Missing tenant context")
        return "default"
    return str(raw).strip()


def get_user_id(request: Request) -> str:
    """Extract ``user_id`` (JWT ``sub`` claim) from the request.

    * OIDC enabled  → missing / empty user is **401 Unauthorized**.
    * OIDC disabled → falls back to ``"anonymous"`` (single-tenant mode).
    """
    raw = getattr(request.state, "user_id", None)
    if not raw or not str(raw).strip():
        if _is_oidc_enabled():
            raise HTTPException(status_code=401, detail="Missing user context")
        return "anonymous"
    return str(raw).strip()


def get_username(request: Request) -> str:
    """Extract ``username`` (JWT ``preferred_username`` claim).

    Falls back to empty string when OIDC is disabled.
    """
    return getattr(request.state, "username", "")


def get_role(request: Request) -> str:
    """Extract ``role`` from the request (set by OIDCAuthMiddleware).

    * OIDC enabled  → falls back to ``"member"`` (least-privilege).
    * OIDC disabled → falls back to ``"admin"`` (dev-friendly, all writes allowed).
    """
    raw = getattr(request.state, "role", None)
    if not raw or not str(raw).strip():
        # In dev mode (no OIDC), grant admin so write endpoints work.
        if not _is_oidc_enabled():
            return "admin"
        return "member"
    role = str(raw).strip()
    # Unrecognised roles are treated as "member" (least-privilege principle).
    if role not in ("owner", "admin", "member"):
        return "member"
    return role


def require_role(*allowed_roles: str):
    """Declarative role guard for FastAPI endpoint dependencies.

    Usage::

        @router.post("/install", dependencies=[require_role("admin", "owner")])
        async def install_skill(...): ...
    """
    def _check(role: str = Depends(get_role)):
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

    return Depends(_check)
