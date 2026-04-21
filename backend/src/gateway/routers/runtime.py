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
from pydantic import BaseModel, ConfigDict, Field

from src.config.agents_config import (
    list_all_agents,
    load_agent_config,
    load_agent_config_layered,
)
from src.config.paths import get_paths
from src.gateway.dependencies import (
    AuthenticatedUser,
    get_tenant_id,
    get_user_id,
    get_user_profile,
    get_username,
)
from src.gateway.runtime_service import (
    RuntimeServiceError,
    create_thread,
    get_thread_state_summary,
    iter_events,
    start_stream,
    stream_message,
)
from src.gateway.thread_context import resolve_thread_context
from src.gateway.thread_registry import get_thread_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runtime", tags=["runtime"])

RequestedOrchestrationMode = Literal["auto", "leader", "workflow"]


# ── Pydantic models ──────────────────────────────────────────────────


class ThreadCreateRequest(BaseModel):
    # DeerFlow web main chat (Phase 1) has no platform session concept, so
    # ``portal_session_id`` is optional — Gateway fills a safe derived value
    # (``deerflow-web:{thread_id}``) when the client omits it. External
    # platform callers that already have a session id keep sending it.
    portal_session_id: str | None = Field(
        default=None, description="Platform session ID (optional for DeerFlow web main chat)"
    )

    # Silently drop any unknown fields (including forged identity fields like
    # ``tenant_id`` / ``user_id`` / ``thread_context`` / ``auth_user``). The
    # router never reads them — identity always comes from auth middleware.
    model_config = ConfigDict(extra="ignore")


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


# ── Safety limits ────────────────────────────────────────────────────
MAX_MESSAGE_LENGTH = 100_000  # 100 KB — generous for multi-page user prompts
MAX_ALLOWED_AGENTS = 100  # no realistic workflow needs more than 100 agents
MAX_GROUP_KEY_LENGTH = 128  # same limit as portal_session_id


# Safe default ``group_key`` for DeerFlow main chat when the client does not
# supply one. External platform callers that care about group scoping pass an
# explicit value.
DEFAULT_GROUP_KEY = "default"


class MessageStreamRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH, description="User message text")
    # DeerFlow main chat has no stable group concept, so ``group_key`` is
    # optional. When omitted, Gateway falls back to ``DEFAULT_GROUP_KEY``.
    group_key: str | None = Field(
        default=None, max_length=MAX_GROUP_KEY_LENGTH, description="Agent group key (defaults to 'default')"
    )
    # DeerFlow main chat has no stable allowed_agents concept either, so this
    # is optional. When omitted, Gateway derives the list from the tenant/user
    # visible agent set (same three-layer resolution the planner uses). The
    # tenant/user visibility guarantee is preserved either way.
    allowed_agents: list[str] | None = Field(
        default=None,
        max_length=MAX_ALLOWED_AGENTS,
        description="Allowed agent names for this request (defaults to tenant/user visible set)",
    )
    entry_agent: str | None = Field(default=None, description="Optional entry agent name")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(
        default=None, description="Orchestration mode hint"
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Optional primitive-only metadata")

    # Silently drop any unknown fields — including forged identity fields
    # (``tenant_id`` / ``user_id`` / ``thread_context`` / ``auth_user`` /
    # ``configurable``). The router never reads the body for identity; it
    # builds ``context`` exclusively from the auth-middleware-resolved
    # tenant/user and Gateway's ``resolve_thread_context(...)``.
    model_config = ConfigDict(extra="ignore")


# ── Validation helpers ───────────────────────────────────────────────


def _resolve_agents_dir(tenant_id: str) -> Path:
    """Return the agents directory for the given tenant (mirrors agents.py)."""
    paths = get_paths()
    if tenant_id and tenant_id != "default":
        return paths.tenant_agents_dir(tenant_id)
    return paths.agents_dir


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


def _resolve_group_key(value: str | None) -> str:
    """Resolve ``group_key`` to a safe value.

    DeerFlow main chat (Phase 1) has no stable group concept, so the client
    may omit the field entirely. When provided, the value must be a non-empty
    string after trimming; when omitted, the Gateway default applies.
    External platform callers that pass an explicit value keep their scoping.
    """
    if value is None:
        return DEFAULT_GROUP_KEY
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="group_key must be non-empty when provided")
    return trimmed


def _resolve_allowed_agents(
    agents: list[str] | None,
    tenant_id: str,
    user_id: str | None = None,
) -> list[str]:
    """Resolve and validate ``allowed_agents``.

    When the client supplies an explicit list, each entry is validated via
    three-layer agent resolution (same code path as the planner/router) and
    must be visible to ``{tenant_id, user_id}``.

    When omitted, the Gateway defaults to the tenant/user visible set —
    this is the α-scheme default for DeerFlow main chat. Visibility is
    preserved either way: a browser can never widen its own reach by
    omitting the field.
    """
    if agents is None:
        visible = list_all_agents(tenant_id=tenant_id, user_id=user_id)
        resolved = [a.name.lower() for a in visible]
        if not resolved:
            raise HTTPException(
                status_code=422,
                detail="No agents are visible for this tenant/user; cannot derive default allowed_agents",
            )
        # Guard against pathologically large tenant agent sets — keep the
        # hard cap consistent with the explicit-list path.
        if len(resolved) > MAX_ALLOWED_AGENTS:
            resolved = resolved[:MAX_ALLOWED_AGENTS]
        return resolved

    return _validate_allowed_agents(agents, tenant_id, user_id=user_id)


def _validate_allowed_agents(agents: list[str], tenant_id: str, user_id: str | None = None) -> list[str]:
    """Validate and deduplicate allowed_agents using three-layer agent resolution.

    An agent is considered valid only if it can actually be loaded via
    ``load_agent_config_layered`` — the same code path the planner/router uses.
    This catches bare directories, missing ``config.yaml``, and malformed YAML.
    """
    if not agents:
        raise HTTPException(status_code=422, detail="allowed_agents must be a non-empty array")

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

        # Verify the agent is fully loadable — same three-layer path as planner/router
        try:
            cfg = load_agent_config_layered(lower, tenant_id=tenant_id, user_id=user_id)
            if cfg is None:
                raise FileNotFoundError(f"Agent '{lower}' not found in any layer")
        except Exception:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown or invalid agent '{trimmed}' in allowed_agents",
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


@router.post("/threads", response_model=ThreadCreateResponse, status_code=201)
async def create_runtime_thread(
    body: ThreadCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """Create a new DeerFlow runtime thread for the platform."""
    # ``portal_session_id`` is optional for DeerFlow web main chat. External
    # callers that already have a session id keep validating / passing it;
    # when omitted, the Gateway derives a stable value from the thread id so
    # the registry binding always has something meaningful to anchor on.
    provided_portal_session_id = (
        _validate_portal_session_id(body.portal_session_id)
        if body.portal_session_id is not None
        else None
    )

    try:
        lg_thread = await create_thread()
    except RuntimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    thread_id = lg_thread["thread_id"]
    portal_session_id = provided_portal_session_id or f"deerflow-web:{thread_id}"

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

    # Ownership validation — returns 403 for unknown or unauthorized threads
    ctx = resolve_thread_context(thread_id, tenant_id, user_id)

    binding = registry.get_binding(thread_id)

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
    auth_user: AuthenticatedUser = Depends(get_user_profile),
):
    """Submit a message to the runtime and stream normalized SSE events."""
    registry = get_thread_registry()

    # Ownership validation — returns 403 for unknown or unauthorized threads
    ctx = resolve_thread_context(thread_id, tenant_id, user_id)

    # Validate payload. ``group_key`` and ``allowed_agents`` accept None and
    # fall back to Gateway-owned defaults suitable for DeerFlow main chat.
    # Tenant/user agent visibility is enforced in both the explicit-list and
    # default-derived paths — a browser cannot widen its own reach by omitting
    # the field, and identity fields in the body are dropped by the model.
    message = _validate_message(body.message)
    group_key = _resolve_group_key(body.group_key)
    allowed_agents = _resolve_allowed_agents(body.allowed_agents, tenant_id, user_id=user_id)
    entry_agent = _validate_entry_agent(body.entry_agent, allowed_agents)
    _validate_metadata(body.metadata)

    # Build runtime context with serialized ThreadContext
    context: dict[str, Any] = {
        "thread_id": thread_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "username": username,
        "allowed_agents": allowed_agents,
        "group_key": group_key,
        "thread_context": ctx.serialize(),
        # Authenticated principal snapshot — downstream identity guard
        # reads this from ``config.configurable["auth_user"]`` to enforce
        # tool-arg identity fields fail-closed.
        "auth_user": {
            "tenant_id": auth_user.tenant_id,
            "user_id": auth_user.user_id,
            "name": auth_user.name,
            "employee_no": auth_user.employee_no,
            "target_system": auth_user.target_system,
            "role": auth_user.role,
        },
    }
    if body.requested_orchestration_mode:
        context["requested_orchestration_mode"] = body.requested_orchestration_mode
    if entry_agent:
        context["agent_name"] = entry_agent

    # Two-phase submission: initiate the upstream run BEFORE committing the
    # HTTP response.  If the upstream rejects (connection refused, 404, 409)
    # this raises RuntimeServiceError which becomes HTTP 503/404/409 — not a
    # 200 SSE stream with an in-band run_failed event.
    try:
        first_chunk, upstream_iter = await start_stream(
            thread_id=thread_id,
            message=message,
            context=context,
        )
    except RuntimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # Upstream accepted — persist binding metadata
    registry.update_binding(
        thread_id,
        group_key=group_key,
        allowed_agents=allowed_agents,
        entry_agent=entry_agent,
        requested_orchestration_mode=body.requested_orchestration_mode,
        metadata=body.metadata,
    )

    return StreamingResponse(
        iter_events(
            thread_id=thread_id,
            first_chunk=first_chunk,
            upstream_iter=upstream_iter,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
