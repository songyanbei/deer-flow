"""ThreadContext — validated identity carrier for thread operations.

After ownership validation, downstream modules receive a ``ThreadContext``
instead of raw ``(tenant_id, user_id, thread_id)`` strings.  This ensures
that every path, sandbox mount, and lifecycle operation operates on an
already-verified identity tuple.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


@dataclass(frozen=True)
class ThreadContext:
    """Immutable, validated identity for a thread operation.

    Only construct via :func:`resolve_thread_context` (HTTP layer) or
    :func:`resolve_thread_context_lenient` (middleware / test layer).
    """

    tenant_id: str
    user_id: str
    thread_id: str

    def serialize(self) -> dict[str, str]:
        """Serialize to a plain dict for ``config.configurable`` transport."""
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
        }

    @classmethod
    def deserialize(cls, d: dict[str, Any]) -> ThreadContext:
        """Reconstruct from a dict (e.g. from ``config.configurable["thread_context"]``)."""
        return cls(
            tenant_id=str(d["tenant_id"]),
            user_id=str(d["user_id"]),
            thread_id=str(d["thread_id"]),
        )


def _validate_ids(tenant_id: str, user_id: str, thread_id: str) -> None:
    """Raise ``ValueError`` if any ID contains unsafe characters."""
    for label, value in [("tenant_id", tenant_id), ("user_id", user_id), ("thread_id", thread_id)]:
        if not _SAFE_ID_RE.match(value):
            raise ValueError(f"Invalid {label}: {value!r}")


def resolve_thread_context(
    thread_id: str,
    tenant_id: str,
    user_id: str,
) -> ThreadContext:
    """Resolve and validate thread ownership, returning a ``ThreadContext``.

    Intended for the HTTP / Gateway layer.  Raises :class:`HTTPException`
    on any validation failure so that routers can use it directly.

    Rules:
    - Unknown thread → 403 (not 404, to prevent resource enumeration).
    - Tenant mismatch → 403.
    - User mismatch → 403.
    - OIDC enabled + binding missing ``user_id`` → 403 (deny legacy entries).

    Raises:
        HTTPException(403): ownership check failed.
        HTTPException(400): invalid ID format.
    """
    try:
        _validate_ids(tenant_id, user_id, thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from src.gateway.thread_registry import get_thread_registry

    binding = get_thread_registry().get_binding(thread_id)
    if binding is None:
        raise HTTPException(status_code=403, detail="Access denied")

    owner_tenant = binding.get("tenant_id")
    if owner_tenant is not None and owner_tenant != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")

    owner_user = binding.get("user_id")
    _oidc_enabled = os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes")

    if _oidc_enabled and owner_user is None:
        # Legacy entry without user_id — deny under OIDC
        raise HTTPException(status_code=403, detail="Access denied")

    if owner_user is not None and owner_user != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return ThreadContext(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)


def resolve_thread_context_lenient(
    thread_id: str,
    tenant_id: str,
    user_id: str,
) -> ThreadContext:
    """Resolve thread ownership without raising HTTP exceptions.

    Intended for middleware and non-HTTP contexts (embedded client, tests).
    Raises ``ValueError`` instead of ``HTTPException`` on failure.
    """
    _validate_ids(tenant_id, user_id, thread_id)

    from src.gateway.thread_registry import get_thread_registry

    binding = get_thread_registry().get_binding(thread_id)
    if binding is None:
        raise ValueError(f"Thread {thread_id!r} not found in registry")

    owner_tenant = binding.get("tenant_id")
    if owner_tenant is not None and owner_tenant != tenant_id:
        raise ValueError(f"Tenant mismatch for thread {thread_id!r}")

    owner_user = binding.get("user_id")
    _oidc_enabled = os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes")

    if _oidc_enabled and owner_user is None:
        raise ValueError(f"Legacy entry without user_id for thread {thread_id!r} (OIDC enabled)")

    if owner_user is not None and owner_user != user_id:
        raise ValueError(f"User mismatch for thread {thread_id!r}")

    return ThreadContext(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
