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
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request


def _is_oidc_enabled() -> bool:
    """Return True when the OIDC middleware is active.

    This mirrors the same env-var check used by ``oidc_config.py`` at startup.
    """
    return os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes")


def _is_sso_enabled() -> bool:
    """Return True when moss-hub SSO authentication is active."""
    return os.getenv("SSO_ENABLED", "false").lower() in ("true", "1", "yes")


def _auth_enabled() -> bool:
    return _is_oidc_enabled() or _is_sso_enabled()


@dataclass(frozen=True)
class AuthenticatedUser:
    """Snapshot of the authenticated request principal.

    Populated from ``request.state`` by :func:`get_user_profile`. Safe to pass
    through ``config.configurable`` into agents / tool wrappers.
    """

    tenant_id: str
    user_id: str
    name: str
    employee_no: str | None
    target_system: str | None
    role: str

    def as_identity(self) -> dict[str, str]:
        """Return the subset of fields that tools are allowed to see."""
        data = {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "name": self.name,
        }
        if self.employee_no:
            data["employee_no"] = self.employee_no
        if self.target_system:
            data["target_system"] = self.target_system
        return data


def get_tenant_id(request: Request) -> str:
    """Extract ``tenant_id`` from the request (set by OIDCAuthMiddleware).

    * Auth enabled  → missing / empty tenant is **401 Unauthorized**.
    * Auth disabled → falls back to ``"default"`` (single-tenant mode).
    """
    raw = getattr(request.state, "tenant_id", None)
    if not raw or not str(raw).strip():
        if _auth_enabled():
            raise HTTPException(status_code=401, detail="Missing tenant context")
        return "default"
    return str(raw).strip()


def get_user_id(request: Request) -> str:
    """Extract ``user_id`` (JWT ``sub`` claim) from the request.

    * Auth enabled  → missing / empty user is **401 Unauthorized**.
    * Auth disabled → falls back to ``"anonymous"`` (single-tenant mode).
    """
    raw = getattr(request.state, "user_id", None)
    if not raw or not str(raw).strip():
        if _auth_enabled():
            raise HTTPException(status_code=401, detail="Missing user context")
        return "anonymous"
    return str(raw).strip()


def get_employee_no(request: Request) -> str | None:
    """Extract ``employee_no`` (moss-hub-issued) from the request state."""
    raw = getattr(request.state, "employee_no", None)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def get_target_system(request: Request) -> str | None:
    raw = getattr(request.state, "target_system", None)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def get_user_profile(request: Request) -> AuthenticatedUser:
    """Return a consolidated :class:`AuthenticatedUser` snapshot.

    Uses ``get_tenant_id`` / ``get_user_id`` fallback semantics so that
    development-mode calls (``OIDC_ENABLED=false`` and ``SSO_ENABLED=false``)
    continue to work with the ``default / anonymous`` principal.
    """
    return AuthenticatedUser(
        tenant_id=get_tenant_id(request),
        user_id=get_user_id(request),
        name=get_username(request),
        employee_no=get_employee_no(request),
        target_system=get_target_system(request),
        role=get_role(request),
    )


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
        # In dev mode (no auth), grant admin so write endpoints work.
        if not _auth_enabled():
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
