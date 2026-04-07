"""Admin API router — lifecycle management endpoints.

All endpoints require the ``admin`` or ``owner`` role.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from src.admin.lifecycle_manager import LifecycleManager
from src.gateway.dependencies import get_tenant_id, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _get_manager() -> LifecycleManager:
    return LifecycleManager()


@router.delete(
    "/users/{user_id}",
    dependencies=[require_role("admin", "owner")],
    summary="Delete all data for a user within the caller's tenant",
)
async def delete_user(
    user_id: str,
    tenant_id: str = Query(..., description="Tenant to scope the deletion"),
    caller_tenant: str = Depends(get_tenant_id),
) -> dict:
    # Prevent cross-tenant deletion: when OIDC is enabled, owner/admin can
    # only manage their own tenant.  When OIDC is off (dev mode), skip check.
    if os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes"):
        if caller_tenant != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot delete users in another tenant")
    result = _get_manager().delete_user(tenant_id, user_id)
    return {
        "status": "partial" if result.has_errors else "ok",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "threads_removed": result.threads_removed,
        "memory_queue_cancelled": result.memory_queue_cancelled,
        "ledger_entries_removed": result.ledger_entries_removed,
        "filesystem_cleaned": result.filesystem_cleaned,
        **({"errors": result.errors} if result.has_errors else {}),
    }


@router.delete(
    "/tenants/{tenant_id}",
    dependencies=[require_role("admin", "owner")],
    summary="Decommission an entire tenant",
)
async def decommission_tenant(
    tenant_id: str,
    caller_tenant: str = Depends(get_tenant_id),
) -> dict:
    # Prevent cross-tenant decommission: when OIDC is enabled, owner/admin can
    # only manage their own tenant.  When OIDC is off (dev mode), skip check.
    if os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes"):
        if caller_tenant != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot decommission another tenant")
    result = _get_manager().decommission_tenant(tenant_id)
    return {
        "status": "partial" if result.has_errors else "ok",
        "tenant_id": tenant_id,
        "threads_removed": result.threads_removed,
        "memory_queue_cancelled": result.memory_queue_cancelled,
        "ledger_entries_removed": result.ledger_entries_removed,
        "mcp_scopes_unloaded": result.mcp_scopes_unloaded,
        "filesystem_cleaned": result.filesystem_cleaned,
        **({"errors": result.errors} if result.has_errors else {}),
    }


@router.post(
    "/cleanup/expired-threads",
    dependencies=[require_role("admin", "owner")],
    summary="Clean up threads older than max_age_seconds within the caller's tenant",
)
async def cleanup_expired_threads(
    max_age_seconds: int = Query(default=604800, ge=3600, description="Max thread age in seconds (default 7 days)"),
    caller_tenant: str = Depends(get_tenant_id),
) -> dict:
    result = _get_manager().cleanup_expired_threads(max_age_seconds, tenant_id=caller_tenant)
    return {
        "status": "ok",
        "tenant_id": caller_tenant,
        "threads_removed": result.threads_removed,
        "max_age_seconds": max_age_seconds,
    }
