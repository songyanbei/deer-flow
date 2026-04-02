"""Platform runtime adapter endpoints.

Provides a stable Gateway integration surface for the external platform to:
1. Create DeerFlow runtime threads
2. Query thread binding/state
3. Submit messages into the multi-agent runtime via SSE streaming

All endpoints are protected by existing OIDC middleware and use
``request.state`` identity only — the request body is never trusted for
identity claims.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config.paths import get_paths
from src.gateway.dependencies import get_tenant_id, get_user_id, get_username
from src.gateway.runtime_service import (
    RuntimeServiceError,
    create_thread,
    get_thread_state_summary,
    stream_message,
)
from src.gateway.thread_registry import get_thread_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runtime", tags=["runtime"])

RequestedOrchestrationMode = Literal["auto", "leader", "workflow"]


# ── Pydantic models ──────────────────────────────────────────────────


class ThreadCreateRequest(BaseModel):
    portal_session_id: str = Field(..., description="Platform session ID")


class ThreadCreateResponse(BaseModel):
    thread_id: str
    portal_session_id: str
    tenant_id: str
    user_id: str
    created_at: str


class ThreadBindingState(BaseModel):
    title: Any = None
    run_id: Any = None
    workflow_stage: Any = None
    workflow_stage_detail: Any = None
    artifacts_count: int = 0
    pending_intervention: bool = False


class ThreadBindingResponse(BaseModel):
    thread_id: str
    portal_session_id: str | None = None
    tenant_id: str
    user_id: str | None = None
    group_key: str | None = None
    allowed_agents: list[str] | None = None
    entry_agent: str | None = None
    requested_orchestration_mode: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    state: ThreadBindingState | None = None


class MessageStreamRequest(BaseModel):
    message: str = Field(..., description="User message text")
    group_key: str = Field(..., description="Agent group key")
    allowed_agents: list[str] = Field(..., description="Allowed agent names for this request")
    entry_agent: str | None = Field(default=None, description="Optional entry agent name")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(
        default=None, description="Orchestration mode hint"
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Optional primitive-only metadata")


# ── Validation helpers ───────────────────────────────────────────────


def _resolve_agents_dir(tenant_id: str) -> Path:
    """Return the agents directory for the given tenant (mirrors agents.py)."""
    paths = get_paths()
    if tenant_id and tenant_id != "default":
        return paths.tenant_agents_dir(tenant_id)
    return paths.agents_dir


def _check_thread_ownership(binding: dict[str, Any], tenant_id: str, user_id: str) -> None:
    """Raise 403 if the caller does not own the thread (tenant + user)."""
    owner_tenant = binding.get("tenant_id")
    if owner_tenant is not None and owner_tenant != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied: thread belongs to another tenant")
    owner_user = binding.get("user_id")
    if owner_user is not None and owner_user != user_id:
        raise HTTPException(status_code=403, detail="Access denied: thread belongs to another user")


def _validate_portal_session_id(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="portal_session_id must be non-empty")
    if len(trimmed) > 128:
        raise HTTPException(status_code=422, detail="portal_session_id exceeds maximum length of 128")
    return trimmed


def _validate_message(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="message must be non-empty")
    return trimmed


def _validate_group_key(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="group_key must be non-empty")
    return trimmed


def _validate_allowed_agents(agents: list[str], tenant_id: str) -> list[str]:
    """Validate and deduplicate allowed_agents against tenant-scoped agent storage."""
    if not agents:
        raise HTTPException(status_code=422, detail="allowed_agents must be a non-empty array")

    agents_dir = _resolve_agents_dir(tenant_id)
    seen: set[str] = set()
    normalized: list[str] = []

    for name in agents:
        trimmed = name.strip()
        if not trimmed:
            raise HTTPException(status_code=422, detail="allowed_agents contains an empty agent name")
        lower = trimmed.lower()
        if lower in seen:
            continue
        seen.add(lower)

        # Verify agent exists in tenant-scoped storage
        agent_dir = agents_dir / lower
        if not agent_dir.is_dir():
            raise HTTPException(
                status_code=422,
                detail=f"Unknown agent '{trimmed}' in allowed_agents",
            )
        normalized.append(lower)

    return normalized


def _validate_entry_agent(entry_agent: str | None, allowed_agents: list[str]) -> str | None:
    if entry_agent is None:
        return None
    trimmed = entry_agent.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="entry_agent must be non-empty when provided")
    lower = trimmed.lower()
    if lower not in allowed_agents:
        raise HTTPException(
            status_code=422,
            detail=f"entry_agent '{trimmed}' must be included in allowed_agents",
        )
    return lower


def _validate_metadata(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    for key, val in meta.items():
        if val is not None and not isinstance(val, (str, int, float, bool)):
            raise HTTPException(
                status_code=422,
                detail=f"metadata['{key}'] must be a primitive JSON value (string, number, boolean, or null)",
            )
    return meta


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/threads", response_model=ThreadCreateResponse)
async def create_runtime_thread(
    body: ThreadCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """Create a new DeerFlow runtime thread for the platform."""
    portal_session_id = _validate_portal_session_id(body.portal_session_id)

    try:
        lg_thread = await create_thread()
    except RuntimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    thread_id = lg_thread["thread_id"]

    registry = get_thread_registry()
    binding = registry.register_binding(
        thread_id,
        tenant_id=tenant_id,
        user_id=user_id,
        portal_session_id=portal_session_id,
    )

    return ThreadCreateResponse(
        thread_id=thread_id,
        portal_session_id=portal_session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        created_at=binding["created_at"],
    )


@router.get("/threads/{thread_id}", response_model=ThreadBindingResponse)
async def get_runtime_thread(
    thread_id: str,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """Return thread binding metadata and current state summary."""
    registry = get_thread_registry()

    binding = registry.get_binding(thread_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Thread not found in registry")

    # Tenant + owner access control
    _check_thread_ownership(binding, tenant_id, user_id)

    # Fetch live state summary from LangGraph
    try:
        state_summary = await get_thread_state_summary(thread_id)
    except RuntimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return ThreadBindingResponse(
        thread_id=thread_id,
        portal_session_id=binding.get("portal_session_id"),
        tenant_id=binding.get("tenant_id", tenant_id),
        user_id=binding.get("user_id"),
        group_key=binding.get("group_key"),
        allowed_agents=binding.get("allowed_agents"),
        entry_agent=binding.get("entry_agent"),
        requested_orchestration_mode=binding.get("requested_orchestration_mode"),
        created_at=binding.get("created_at"),
        updated_at=binding.get("updated_at"),
        state=ThreadBindingState(**state_summary),
    )


@router.post("/threads/{thread_id}/messages:stream")
async def stream_runtime_message(
    thread_id: str,
    body: MessageStreamRequest,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
    username: str = Depends(get_username),
):
    """Submit a message to the runtime and stream normalized SSE events."""
    registry = get_thread_registry()

    # Thread existence + ownership check
    binding = registry.get_binding(thread_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Thread not found in registry")

    _check_thread_ownership(binding, tenant_id, user_id)

    # Validate payload
    message = _validate_message(body.message)
    group_key = _validate_group_key(body.group_key)
    allowed_agents = _validate_allowed_agents(body.allowed_agents, tenant_id)
    entry_agent = _validate_entry_agent(body.entry_agent, allowed_agents)
    _validate_metadata(body.metadata)

    # Build runtime context
    context: dict[str, Any] = {
        "thread_id": thread_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "username": username,
        "allowed_agents": allowed_agents,
        "group_key": group_key,
    }
    if body.requested_orchestration_mode:
        context["requested_orchestration_mode"] = body.requested_orchestration_mode
    if entry_agent:
        context["agent_name"] = entry_agent

    def _persist_binding_on_submit_success() -> None:
        registry.update_binding(
            thread_id,
            group_key=group_key,
            allowed_agents=allowed_agents,
            entry_agent=entry_agent,
            requested_orchestration_mode=body.requested_orchestration_mode,
        )

    return StreamingResponse(
        stream_message(
            thread_id=thread_id,
            message=message,
            context=context,
            on_submit_success=_persist_binding_on_submit_success,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
