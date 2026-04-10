"""Promotion API — submit, list, and resolve promotion requests.

Users submit promotions via ``/api/me/agents/{name}:promote`` or
``/api/me/skills/{name}:promote``.  Admins list and resolve via
``/api/promotions``.  Non-admin users can only see their own requests.
"""

from __future__ import annotations

import logging
import shutil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config.paths import get_paths
from src.gateway.dependencies import get_role, get_tenant_id, get_user_id, require_role
from src.promotion.store import get_promotion_store
from src.promotion.types import PromotionStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["promotions"])


# ── Pydantic models ─────────────────────────────────────────────────────


class PromotionRequestResponse(BaseModel):
    request_id: str
    tenant_id: str
    user_id: str
    resource_type: str
    resource_name: str
    target_name: str
    status: str
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None
    reason: str | None = None


class PromotionListResponse(BaseModel):
    requests: list[PromotionRequestResponse]


class PromotionSubmitRequest(BaseModel):
    target_name: str | None = Field(default=None, description="Optional target name at tenant layer (defaults to resource name)")


class PromotionResolveRequest(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, description="Optional admin comment")


# ── Dependency guard for identified users ───────────────────────────────


def _require_identified_user(
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
) -> tuple[str, str]:
    if not tenant_id or tenant_id == "default" or not user_id or user_id == "anonymous":
        raise HTTPException(status_code=403, detail="Promotion endpoints require an identified user")
    return tenant_id, user_id


# ── Submit endpoints (user-facing, under /api/me) ──────────────────────


me_router = APIRouter(prefix="/api/me", tags=["me"])


@me_router.post("/agents/{name}:promote", response_model=PromotionRequestResponse, status_code=201)
async def promote_personal_agent(
    name: str,
    body: PromotionSubmitRequest | None = None,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PromotionRequestResponse:
    """Submit a promotion request for a personal agent to the tenant layer."""
    tenant_id, user_id = identity
    name_lower = name.lower()

    # Verify the personal agent exists
    agent_dir = get_paths().tenant_user_agent_dir(tenant_id, user_id, name_lower)
    if not agent_dir.exists() or not (agent_dir / "config.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")

    target_name = (body.target_name or name_lower).lower() if body else name_lower

    store = get_promotion_store()
    try:
        req = store.submit(tenant_id, user_id, "agent", name_lower, target_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return PromotionRequestResponse(**req)


@me_router.post("/skills/{name}:promote", response_model=PromotionRequestResponse, status_code=201)
async def promote_personal_skill(
    name: str,
    body: PromotionSubmitRequest | None = None,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PromotionRequestResponse:
    """Submit a promotion request for a personal skill to the tenant layer."""
    tenant_id, user_id = identity

    # Verify the personal skill exists
    skill_dir = get_paths().tenant_user_skills_dir(tenant_id, user_id) / name
    if not skill_dir.exists() or not (skill_dir / "SKILL.md").exists():
        raise HTTPException(status_code=404, detail=f"Personal skill '{name}' not found")

    target_name = (body.target_name or name) if body else name

    store = get_promotion_store()
    try:
        req = store.submit(tenant_id, user_id, "skill", name, target_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return PromotionRequestResponse(**req)


# ── Admin list/resolve endpoints ────────────────────────────────────────


@router.get("/promotions", response_model=PromotionListResponse)
async def list_promotions(
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
    role: str = Depends(get_role),
) -> PromotionListResponse:
    """List promotion requests.  Admins see all; members see only their own."""
    if not tenant_id or tenant_id == "default":
        raise HTTPException(status_code=403, detail="Promotions require a non-default tenant")

    store = get_promotion_store()
    if role in ("admin", "owner"):
        reqs = store.list_all(tenant_id)
    else:
        reqs = store.list_by_user(tenant_id, user_id)

    return PromotionListResponse(requests=[PromotionRequestResponse(**r) for r in reqs])


@router.post(
    "/promotions/{request_id}:resolve",
    response_model=PromotionRequestResponse,
    dependencies=[require_role("admin", "owner")],
)
async def resolve_promotion(
    request_id: str,
    body: PromotionResolveRequest,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
) -> PromotionRequestResponse:
    """Approve or reject a pending promotion request.

    On approval, the personal resource directory is copied to the tenant layer.
    If a same-name resource already exists at the tenant layer, the request is rejected.
    """
    if not tenant_id or tenant_id == "default":
        raise HTTPException(status_code=403, detail="Promotions require a non-default tenant")

    store = get_promotion_store()
    req = store.get(tenant_id, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Promotion request '{request_id}' not found")

    status = PromotionStatus.APPROVED if body.action == "approve" else PromotionStatus.REJECTED

    # Resolve the store record FIRST (atomic status transition).
    # This prevents the race where copytree succeeds but resolve fails
    # (e.g. concurrent approval), leaving an orphan directory at tenant layer.
    try:
        updated = store.resolve(tenant_id, request_id, status, resolved_by=user_id, reason=body.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # On approval, copy the resource to tenant layer AFTER successful resolve.
    if status == PromotionStatus.APPROVED:
        paths = get_paths()
        resource_type = req.get("resource_type")
        resource_name = req.get("resource_name", "")
        target_name = req.get("target_name", resource_name)
        source_user_id = req.get("user_id", "")

        if resource_type == "agent":
            src_dir = paths.tenant_user_agent_dir(tenant_id, source_user_id, resource_name)
            dst_dir = paths.tenant_agents_dir(tenant_id) / target_name.lower()
        elif resource_type == "skill":
            src_dir = paths.tenant_user_skills_dir(tenant_id, source_user_id) / resource_name
            dst_dir = paths.tenant_dir(tenant_id) / "skills" / target_name
        else:
            # Should not happen — store already resolved, but guard anyway.
            raise HTTPException(status_code=400, detail=f"Unknown resource type: {resource_type}")

        if not src_dir.exists():
            raise HTTPException(status_code=410, detail=f"Source {resource_type} '{resource_name}' no longer exists")
        if dst_dir.exists():
            raise HTTPException(status_code=409, detail=f"Tenant {resource_type} '{target_name}' already exists — reject or use a different target name")

        try:
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dst_dir)
            logger.info("Promoted %s '%s' → '%s' at tenant layer (tenant=%s)", resource_type, resource_name, target_name, tenant_id)
        except Exception:
            # Rollback: clean up partially copied directory
            if dst_dir.exists():
                shutil.rmtree(dst_dir, ignore_errors=True)
            raise

    return PromotionRequestResponse(**updated)
