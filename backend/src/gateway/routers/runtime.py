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

from src.agents.governance.ledger import governance_ledger
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
    start_governance_resume_stream,
    start_resume_stream,
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


class ClarificationAnswer(BaseModel):
    """One answer inside a workflow clarification response payload."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="User's answer text for this question")


class WorkflowClarificationResponse(BaseModel):
    """Envelope for the user's clarification answers on a suspended workflow task."""

    model_config = ConfigDict(extra="forbid")

    answers: dict[str, ClarificationAnswer] = Field(
        ..., description="Answers keyed by clarification question id"
    )


class AppRuntimeContext(BaseModel):
    """App-level runtime flags forwarded into the LangGraph ``context``.

    These are per-run, per-submit fields that drive model selection,
    middleware wiring, and workflow clarification resume. Identity fields
    (``tenant_id`` / ``user_id`` / ``thread_id`` / ``thread_context`` /
    ``auth_user``) are **intentionally absent** from this schema — identity
    is always resolved from the auth middleware and ``resolve_thread_context``.
    ``extra="forbid"`` ensures any attempt to smuggle identity (or unknown)
    fields through here surfaces as HTTP 422 rather than being silently
    merged.
    """

    model_config = ConfigDict(extra="forbid")

    thinking_enabled: bool | None = Field(
        default=None, description="Enable thinking-capable model selection"
    )
    is_plan_mode: bool | None = Field(
        default=None, description="Enable TodoListMiddleware + write_todos tool"
    )
    subagent_enabled: bool | None = Field(
        default=None, description="Enable the task delegation tool + SubagentLimitMiddleware"
    )
    is_bootstrap: bool | None = Field(
        default=None, description="Agent bootstrap marker for /agents/{name}/chats/new"
    )
    workflow_clarification_resume: bool | None = Field(
        default=None, description="Mark this submit as a workflow clarification resume"
    )
    workflow_resume_run_id: str | None = Field(
        default=None, description="Run id of the suspended workflow to resume"
    )
    workflow_resume_task_id: str | None = Field(
        default=None, description="Task id within the resumed run"
    )
    workflow_clarification_response: WorkflowClarificationResponse | None = Field(
        default=None, description="Clarification answers payload for the resume path"
    )


# Keys that the router builds server-side and must never be overwritten by
# ``app_context``. The merge order already keeps server identity fields on
# top, but we also drop any client-supplied key matching this set as a
# belt-and-braces defense (in case AppRuntimeContext schema ever gains a
# conflicting name). Kept as a module constant so tests can import it.
_IDENTITY_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "thread_id",
        "tenant_id",
        "user_id",
        "username",
        "allowed_agents",
        "group_key",
        "thread_context",
        "auth_user",
        "agent_name",
        "requested_orchestration_mode",
    }
)


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
    # App-level runtime flags forwarded into LangGraph ``context`` (per-run,
    # not per-thread). Validated via ``extra="forbid"`` on its own model so
    # unknown keys surface as 422 instead of being silently dropped — that
    # was the regression the frontend hit when Gateway submit replaced the
    # native ``thread.submit({context})`` path.
    app_context: AppRuntimeContext | None = Field(
        default=None, description="App-level runtime flags (thinking/plan/subagent/workflow-resume)"
    )

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


# ── Resume request model ──────────────────────────────────────────────


# Resume ``Command.goto`` whitelist — currently empty because the Gateway
# intervention resume path relies on checkpoint + resume message only. Any
# browser-supplied ``goto`` is rejected as a server-side route-bypass attempt.
# Extend this set deliberately when a new resume flow is introduced.
_ALLOWED_RESUME_GOTO: frozenset[str] = frozenset()


MAX_RESUME_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH


class ResumePayload(BaseModel):
    """Nested ``resume_payload`` body shape documented in the Phase 2.1 spec.

    The legacy InterventionCard contract wraps the resume message inside a
    ``resume_payload`` object. Phase 2.1 accepts either that nested form or a
    top-level ``message``; if both are given, the top-level wins so the
    contract stays strict about which field the caller intended.
    """

    model_config = ConfigDict(extra="forbid")

    message: str | None = Field(
        default=None,
        max_length=MAX_RESUME_MESSAGE_LENGTH,
        description="Human resume text (equivalent to top-level ``message``)",
    )


class ResumeStreamRequest(BaseModel):
    """Request body for ``POST /api/runtime/threads/{id}/resume``.

    The Gateway mirrors the legacy ``thread.submit({...})`` contract the
    frontend ``InterventionCard`` used against ``/api/langgraph``, minus any
    identity channel. The browser may pass:

    - ``message`` — human resume text (same semantics as the legacy path).
    - ``checkpoint`` — forwarded to LangGraph to anchor the resume to the
      interrupted run's checkpoint chain.
    - ``interrupt_feedback`` — becomes ``Command.resume`` when present.
    - ``goto`` — validated against a Gateway-owned whitelist. Currently
      empty, so any non-None value is rejected.
    - ``workflow_clarification_resume`` / ``workflow_resume_run_id`` /
      ``workflow_resume_task_id`` — trusted-context hints forwarded into
      LangGraph ``context`` so the workflow resume node can continue the
      interrupted task instead of starting a fresh message.
    - ``app_context`` — per-run flags (thinking/plan/subagent) same as the
      ``messages:stream`` path.

    Identity fields (``tenant_id`` / ``user_id`` / ``thread_context`` /
    ``auth_user`` / ``config`` / ``configurable``) are dropped by
    ``extra="ignore"`` — the router never reads the body for identity.
    """

    model_config = ConfigDict(extra="ignore")

    message: str | None = Field(
        default=None,
        max_length=MAX_RESUME_MESSAGE_LENGTH,
        description="Optional human resume message appended as the next turn",
    )
    checkpoint: dict[str, Any] | None = Field(
        default=None,
        description="LangGraph checkpoint to anchor the resume (opaque to Gateway)",
    )
    interrupt_feedback: Any = Field(
        default=None,
        description="Resume value delivered to the upstream interrupt() caller (Command.resume)",
    )
    goto: str | None = Field(
        default=None,
        description="Optional Command.goto node name (must be in backend whitelist)",
    )
    workflow_clarification_resume: bool | None = Field(
        default=None, description="Mark this submit as a workflow clarification resume"
    )
    workflow_resume_run_id: str | None = Field(
        default=None, description="Run id of the suspended workflow to resume"
    )
    workflow_resume_task_id: str | None = Field(
        default=None, description="Task id within the resumed run"
    )
    app_context: AppRuntimeContext | None = Field(
        default=None,
        description="App-level runtime flags (thinking/plan/subagent/workflow-resume)",
    )
    resume_payload: ResumePayload | None = Field(
        default=None,
        description="Nested payload carrying ``message`` — accepted for compat with the legacy InterventionCard contract",
    )


def _validate_resume_goto(value: str | None) -> str | None:
    """Return a normalized ``goto`` if it is allow-listed, else raise 422."""
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="goto must be non-empty when provided")
    if trimmed not in _ALLOWED_RESUME_GOTO:
        raise HTTPException(
            status_code=422,
            detail=f"goto '{trimmed}' is not allowed by the resume contract",
        )
    return trimmed


def _validate_resume_message(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="message must be non-empty when provided")
    return trimmed


def _validate_checkpoint(checkpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Basic shape guard — checkpoint is opaque, but we reject identity smuggling."""
    if checkpoint is None:
        return None
    if not isinstance(checkpoint, dict):
        raise HTTPException(status_code=422, detail="checkpoint must be a JSON object")
    # Checkpoint is forwarded to LangGraph as-is. Strip any keys that collide
    # with server-sourced identity fields — belt-and-braces against schema drift.
    return {k: v for k, v in checkpoint.items() if k not in _IDENTITY_CONTEXT_KEYS}


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

    # Start with app-level runtime flags (per-run state). These drive model
    # selection, middleware wiring, and workflow clarification resume.
    # ``exclude_none=True`` means only fields the client explicitly set are
    # forwarded — absent fields stay absent downstream (same semantics as
    # the legacy ``thread.submit({context})`` path).
    context: dict[str, Any] = {}
    if body.app_context is not None:
        app_fields = body.app_context.model_dump(exclude_none=True)
        # Drop any key that collides with a server-sourced identity field.
        # ``extra="forbid"`` on AppRuntimeContext already rejects identity
        # keys at the pydantic layer (they are not declared fields), so in
        # practice ``app_fields`` never contains them — this is a second
        # line of defense that also protects against future schema drift.
        for key in list(app_fields.keys()):
            if key in _IDENTITY_CONTEXT_KEYS:
                app_fields.pop(key, None)
        context.update(app_fields)

    # Server-sourced identity + routing fields. Written AFTER ``app_context``
    # so any client attempt to smuggle identity (via schema drift or future
    # additions) is overwritten here. ``resolve_thread_context`` already
    # enforced tenant/user ownership of ``thread_id`` above.
    context.update(
        {
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
    )
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


@router.post("/threads/{thread_id}/resume")
async def resume_runtime_stream(
    thread_id: str,
    body: ResumeStreamRequest,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
    username: str = Depends(get_username),
    auth_user: AuthenticatedUser = Depends(get_user_profile),
):
    """Resume an interrupted runtime thread via trusted Gateway SSE.

    This is the Phase 2 successor to the browser ``thread.submit(..., {
    checkpoint, context: { workflow_resume_* } })`` path used by
    ``InterventionCard``. The resume is anchored to the caller-supplied
    checkpoint, trusted identity is injected server-side, and upstream
    ``config.configurable`` is never populated — LG1.x rejects dual-channel.
    """
    registry = get_thread_registry()

    # Ownership validation — returns 403 for unknown or unauthorized threads.
    ctx = resolve_thread_context(thread_id, tenant_id, user_id)

    # Validate body fields. Browser-supplied identity has already been dropped
    # by ``extra="ignore"``; these validators only shape-check the remaining
    # fields before we build trusted context.
    # Accept either ``message`` at the top level or the nested
    # ``resume_payload.message`` form documented in the Phase 2.1 spec. Top
    # level wins when both are present — the contract is explicit rather than
    # silently merging.
    raw_message = body.message
    if raw_message is None and body.resume_payload is not None:
        raw_message = body.resume_payload.message
    message = _validate_resume_message(raw_message)
    goto = _validate_resume_goto(body.goto)
    checkpoint = _validate_checkpoint(body.checkpoint)

    # Build LangGraph ``context`` — identical construction to ``messages:stream``
    # so the state projection driving ``liveValuesPatch`` converges. App-level
    # flags (thinking/plan/subagent) applied first, then server-sourced identity
    # overwrites anything colliding.
    context: dict[str, Any] = {}
    if body.app_context is not None:
        app_fields = body.app_context.model_dump(exclude_none=True)
        for key in list(app_fields.keys()):
            if key in _IDENTITY_CONTEXT_KEYS:
                app_fields.pop(key, None)
        context.update(app_fields)

    # Workflow resume hints are trusted-context business fields — they drive
    # the workflow resume node to continue the interrupted task rather than
    # starting a fresh message. Same shape the legacy front-end path used.
    if body.workflow_clarification_resume is not None:
        context["workflow_clarification_resume"] = body.workflow_clarification_resume
    if body.workflow_resume_run_id is not None:
        context["workflow_resume_run_id"] = body.workflow_resume_run_id
    if body.workflow_resume_task_id is not None:
        context["workflow_resume_task_id"] = body.workflow_resume_task_id

    context.update(
        {
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "username": username,
            "thread_context": ctx.serialize(),
            "auth_user": {
                "tenant_id": auth_user.tenant_id,
                "user_id": auth_user.user_id,
                "name": auth_user.name,
                "employee_no": auth_user.employee_no,
                "target_system": auth_user.target_system,
                "role": auth_user.role,
            },
        }
    )

    # Fall back to the registry-bound routing fields when the interrupted run
    # had them — resume inherits the original run's ``group_key`` /
    # ``allowed_agents`` / ``entry_agent`` rather than letting the browser
    # widen scope mid-run.
    binding = registry.get_binding(thread_id) or {}
    bound_group_key = binding.get("group_key")
    if bound_group_key:
        context["group_key"] = bound_group_key
    bound_allowed_agents = binding.get("allowed_agents")
    if bound_allowed_agents:
        context["allowed_agents"] = list(bound_allowed_agents)
    bound_entry_agent = binding.get("entry_agent")
    if bound_entry_agent:
        context["agent_name"] = bound_entry_agent
    bound_mode = binding.get("requested_orchestration_mode")
    if bound_mode:
        context["requested_orchestration_mode"] = bound_mode

    # Build the ``Command`` payload when the caller supplied resume semantics.
    # The intervention card path currently uses message+checkpoint only, but
    # the spec requires the endpoint to accept ``interrupt_feedback`` /
    # ``goto`` for future flows.
    command: dict[str, Any] | None = None
    if body.interrupt_feedback is not None or goto is not None:
        command = {}
        if body.interrupt_feedback is not None:
            command["resume"] = body.interrupt_feedback
        if goto is not None:
            command["goto"] = goto

    if message is None and command is None:
        # Nothing for the upstream to do. Prefer a structured 422 over a
        # confusing upstream rejection.
        raise HTTPException(
            status_code=422,
            detail="resume request must include at least one of: message, interrupt_feedback, goto",
        )

    try:
        first_chunk, upstream_iter = await start_resume_stream(
            thread_id=thread_id,
            context=context,
            message=message,
            checkpoint=checkpoint,
            command=command,
        )
    except RuntimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

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


# ── Governance resume ────────────────────────────────────────────────
#
# Phase 2.2 (D2.2 / Task 2.2): trusted Gateway wrapper around the legacy
# ``client.runs.create(...)`` path the browser uses from
# ``frontend/src/core/governance/utils.ts::buildGovernanceResumeRequest``.
#
# Governance resume is semantically a **fresh human message** that carries
# workflow-resume markers so the workflow graph resumes the governance-held
# task rather than starting a new turn. Unlike intervention resume it never
# ships a ``checkpoint`` or a ``Command`` (no interrupt to answer) — it
# appends a resume message to the thread's latest checkpoint chain.
#
# Trust model:
# - Identity (tenant_id/user_id/thread_context/auth_user) is resolved from
#   auth middleware + ``resolve_thread_context``; the request body is never
#   read for identity. ``extra="ignore"`` drops forged identity keys.
# - ``workflow_clarification_resume`` is **forced to True** server-side so
#   the browser cannot flip the marker.
# - ``governance_id`` is forwarded into trusted context so downstream audit
#   / ledger handlers can correlate the resume without trusting body scalars
#   as routing keys beyond that.


class GovernanceResumeRequest(BaseModel):
    """Request body for ``POST /api/runtime/threads/{id}/governance:resume``.

    Mirrors the legacy ``buildGovernanceResumeRequest`` contract minus any
    identity channel:

    - ``message`` — human resume text (required; governance resume always
      appends a reviewer message).
    - ``governance_id`` — governance ledger entry id the resume is attached
      to. Forwarded into trusted context; never used as an identity key.
    - ``workflow_resume_run_id`` / ``workflow_resume_task_id`` — optional
      workflow-correlation hints matching the ledger's ``run_id`` /
      ``task_id`` so the workflow graph resumes the right task.
    - ``app_context`` — per-run flags (``thinking_enabled`` / ``is_plan_mode``
      / ``subagent_enabled``). ``extra="forbid"`` keeps identity smuggling
      visible as HTTP 422.

    Intentionally **not** present:
    - ``checkpoint`` — governance resume is not checkpoint-anchored.
    - ``goto`` / ``interrupt_feedback`` — governance resume is a fresh
      message, not an interrupt ``Command`` reply.
    - ``workflow_clarification_resume`` — forced to ``True`` server-side.
    """

    model_config = ConfigDict(extra="ignore")

    message: str = Field(
        ...,
        min_length=1,
        max_length=MAX_RESUME_MESSAGE_LENGTH,
        description="Human resume text written by the reviewer",
    )
    governance_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Governance ledger entry id being resumed",
    )
    workflow_resume_run_id: str | None = Field(
        default=None, description="Run id of the governance-held workflow run"
    )
    workflow_resume_task_id: str | None = Field(
        default=None, description="Task id of the governance-held workflow task"
    )
    app_context: AppRuntimeContext | None = Field(
        default=None,
        description="App-level runtime flags (thinking/plan/subagent)",
    )


def _validate_governance_message(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="message must be non-empty")
    return trimmed


def _validate_governance_id(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="governance_id must be non-empty")
    return trimmed


@router.post("/threads/{thread_id}/governance:resume")
async def resume_governance_stream(
    thread_id: str,
    body: GovernanceResumeRequest,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
    username: str = Depends(get_username),
    auth_user: AuthenticatedUser = Depends(get_user_profile),
):
    """Resume a governance-held workflow via trusted Gateway SSE.

    Phase 2.2 successor to the browser ``client.runs.create(...)`` path in
    ``frontend/src/core/governance/utils.ts``. Identity is server-sourced,
    upstream submit is context-only (LG1.x dual-channel rejected), and the
    normalized SSE projection is shared with ``messages:stream`` / ``resume``
    so ``liveValuesPatch`` converges.
    """
    registry = get_thread_registry()

    # Ownership validation — 403 for unknown/unauthorized threads.
    ctx = resolve_thread_context(thread_id, tenant_id, user_id)

    message = _validate_governance_message(body.message)
    governance_id = _validate_governance_id(body.governance_id)

    # Ledger authorization — fail-closed. The ledger is authoritative for
    # governance correlation: we never trust governance_id / run_id / task_id
    # from the browser without a matching pending ledger entry that belongs to
    # this tenant/user/thread.
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="governance entry not found")
    if entry.get("thread_id") != thread_id:
        raise HTTPException(
            status_code=403, detail="governance entry does not belong to this thread"
        )
    if entry.get("tenant_id", "default") != tenant_id:
        raise HTTPException(
            status_code=403, detail="governance entry does not belong to this tenant"
        )
    if entry.get("user_id") != user_id:
        raise HTTPException(
            status_code=403, detail="governance entry does not belong to this user"
        )
    # Resumable statuses. The real flow is:
    #   1. POST /api/governance/{id}:resolve  → ledger flips pending → resolved
    #   2. POST /api/runtime/threads/{id}/governance:resume
    # so ``resolved`` is the normal state at resume time. ``pending_intervention``
    # is allowed to cover a race where resume races the resolve commit. Terminal
    # failure states (``rejected`` / ``failed`` / ``expired``) and ``decided``
    # (immediate allow/deny, no human interrupt to resume) are fail-closed.
    _RESUMABLE_STATUSES = {"pending_intervention", "resolved"}
    if entry.get("status") not in _RESUMABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"governance entry is not resumable (status={entry.get('status')})",
        )
    ledger_run_id = entry.get("run_id")
    ledger_task_id = entry.get("task_id")
    if body.workflow_resume_run_id is not None and body.workflow_resume_run_id != ledger_run_id:
        raise HTTPException(
            status_code=422, detail="workflow_resume_run_id does not match ledger entry"
        )
    if body.workflow_resume_task_id is not None and body.workflow_resume_task_id != ledger_task_id:
        raise HTTPException(
            status_code=422, detail="workflow_resume_task_id does not match ledger entry"
        )

    # Build trusted LangGraph ``context``. App flags first, then server-sourced
    # identity overwrites any collision, then registry-bound routing is
    # inherited (never widened by the browser).
    context: dict[str, Any] = {}
    if body.app_context is not None:
        app_fields = body.app_context.model_dump(exclude_none=True)
        for key in list(app_fields.keys()):
            if key in _IDENTITY_CONTEXT_KEYS:
                app_fields.pop(key, None)
        context.update(app_fields)

    # Governance resume always marks the submit as a workflow clarification
    # resume — the browser cannot flip this off. workflow_resume_* values are
    # sourced from the ledger entry (authoritative), never from the request
    # body, so a tampered body cannot redirect the resume to a different run.
    context["workflow_clarification_resume"] = True
    if ledger_run_id is not None:
        context["workflow_resume_run_id"] = ledger_run_id
    if ledger_task_id is not None:
        context["workflow_resume_task_id"] = ledger_task_id
    # TODO(D2.x audit): ``governance_id`` is forwarded into trusted context for a
    # future governance-resumed audit hook to correlate the resume with its
    # ledger entry. No consumer reads it today — audit integrity on Phase 2.2
    # currently relies on workflow_resume_run_id / workflow_resume_task_id
    # anchoring, same as the legacy browser path. Keep writing the field so the
    # downstream hook can be added without a contract change.
    context["governance_id"] = governance_id

    context.update(
        {
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "username": username,
            "thread_context": ctx.serialize(),
            "auth_user": {
                "tenant_id": auth_user.tenant_id,
                "user_id": auth_user.user_id,
                "name": auth_user.name,
                "employee_no": auth_user.employee_no,
                "target_system": auth_user.target_system,
                "role": auth_user.role,
            },
        }
    )

    binding = registry.get_binding(thread_id) or {}
    bound_group_key = binding.get("group_key")
    if bound_group_key:
        context["group_key"] = bound_group_key
    bound_allowed_agents = binding.get("allowed_agents")
    if bound_allowed_agents:
        context["allowed_agents"] = list(bound_allowed_agents)
    bound_entry_agent = binding.get("entry_agent")
    if bound_entry_agent:
        context["agent_name"] = bound_entry_agent
    bound_mode = binding.get("requested_orchestration_mode")
    if bound_mode:
        context["requested_orchestration_mode"] = bound_mode

    # Mirror the legacy ``buildGovernanceResumeRequest`` contract: workflow
    # runs keep ``stream_subgraphs=False`` so the outer workflow transcript
    # stays clean; leader / auto / unset modes enable subgraph streaming so
    # governance reviewers still see subagent progress events after resume.
    # Derived server-side from the bound mode — the browser never gets to
    # flip this toggle.
    stream_subgraphs = bound_mode != "workflow"

    try:
        first_chunk, upstream_iter = await start_governance_resume_stream(
            thread_id=thread_id,
            context=context,
            message=message,
            stream_subgraphs=stream_subgraphs,
        )
    except RuntimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

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
