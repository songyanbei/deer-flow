"""FastAPI dependency helpers for tenant-aware endpoints.

These dependencies extract identity context injected by ``OIDCAuthMiddleware``
and provide it to router endpoints via FastAPI's dependency injection system.

When OIDC is disabled, ``request.state`` has no identity attributes.  In that
case these dependencies fall back to sensible defaults so the application
continues to work in single-tenant / development mode.
"""

from __future__ import annotations

from fastapi import Request


def get_tenant_id(request: Request) -> str:
    """Extract ``tenant_id`` from the request (set by OIDCAuthMiddleware).

    Falls back to ``"default"`` when OIDC is disabled, preserving backward
    compatibility for single-tenant deployments.
    """
    return getattr(request.state, "tenant_id", "default")


def get_user_id(request: Request) -> str:
    """Extract ``user_id`` (JWT ``sub`` claim) from the request.

    Falls back to ``"anonymous"`` when OIDC is disabled.
    """
    return getattr(request.state, "user_id", "anonymous")


def get_username(request: Request) -> str:
    """Extract ``username`` (JWT ``preferred_username`` claim).

    Falls back to empty string when OIDC is disabled.
    """
    return getattr(request.state, "username", "")
