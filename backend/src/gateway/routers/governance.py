"""Governance Operator Console API — Stage 5B.

Provides queue, detail, history, and operator action endpoints backed by the
governance ledger (Stage 5A).  These endpoints power the operator console UI
without depending on thread message text parsing.

Endpoints:
    GET  /api/governance/queue              — pending items with filters
    GET  /api/governance/history            — resolved/rejected/failed/expired items
    GET  /api/governance/{governance_id}    — single item detail
    POST /api/governance/{governance_id}:resolve — operator action (reuses resolution contract)
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.agents.governance.ledger import governance_ledger
from src.agents.governance.types import GovernanceLedgerEntry
from src.gateway.dependencies import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/governance",
    tags=["governance"],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class GovernanceItemResponse(BaseModel):
    """Single governance item for list and detail views."""

    governance_id: str
    thread_id: str
    run_id: str
    task_id: str
    source_agent: str
    hook_name: str
    source_path: str
    risk_level: str
    category: str
    decision: str
    status: str
    rule_id: str | None = None
    request_id: str | None = None
    action_summary: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None

    # Detail-only: extracted from metadata for convenience
    intervention_title: str | None = None
    intervention_tool_name: str | None = None
    intervention_display: dict[str, Any] | list[Any] | None = None
    intervention_action_schema: dict[str, Any] | None = None
    intervention_fingerprint: str | None = None

    @classmethod
    def from_entry(cls, entry: GovernanceLedgerEntry, *, include_detail: bool = False) -> "GovernanceItemResponse":
        """Build response from ledger entry.

        When *include_detail* is True, intervention display/action context is
        extracted from the entry's metadata and surfaced as top-level fields.
        """
        meta = entry.get("metadata") or {}

        detail_fields: dict[str, Any] = {}
        if include_detail:
            detail_fields["intervention_title"] = meta.get("intervention_title")
            detail_fields["intervention_tool_name"] = meta.get("intervention_tool_name")
            detail_fields["intervention_display"] = meta.get("intervention_display")
            detail_fields["intervention_action_schema"] = meta.get("intervention_action_schema")
            detail_fields["intervention_fingerprint"] = meta.get("intervention_fingerprint")

        return cls(
            governance_id=entry["governance_id"],
            thread_id=entry["thread_id"],
            run_id=entry["run_id"],
            task_id=entry["task_id"],
            source_agent=entry["source_agent"],
            hook_name=entry["hook_name"],
            source_path=entry["source_path"],
            risk_level=entry["risk_level"],
            category=entry["category"],
            decision=entry["decision"],
            status=entry["status"],
            rule_id=entry.get("rule_id"),
            request_id=entry.get("request_id"),
            action_summary=entry.get("action_summary"),
            reason=entry.get("reason"),
            metadata=meta,
            created_at=entry["created_at"],
            resolved_at=entry.get("resolved_at"),
            resolved_by=entry.get("resolved_by"),
            **detail_fields,
        )


class GovernanceListResponse(BaseModel):
    """Paginated list of governance items."""

    items: list[GovernanceItemResponse]
    total: int
    limit: int
    offset: int


class OperatorResolveRequest(BaseModel):
    """Request body for operator resolve action."""

    action_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str | None = None


class OperatorResolveResponse(BaseModel):
    """Response from operator resolve action."""

    ok: bool = True
    governance_id: str
    status: str
    resume_action: str | None = None
    resume_payload: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Queue API — pending items
# ---------------------------------------------------------------------------

@router.get("/queue", response_model=GovernanceListResponse)
async def list_queue(
    request: Request,
    thread_id: str | None = Query(None, description="Filter by thread ID"),
    run_id: str | None = Query(None, description="Filter by run ID"),
    risk_level: str | None = Query(None, description="Filter by risk level (medium/high/critical)"),
    source_agent: str | None = Query(None, description="Filter by source agent"),
    created_from: str | None = Query(None, description="Filter by created_at >= ISO datetime"),
    created_to: str | None = Query(None, description="Filter by created_at <= ISO datetime"),
    limit: int = Query(50, ge=1, le=500, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    tenant_id: str = Depends(get_tenant_id),
) -> GovernanceListResponse:
    """List pending governance items (operator queue).

    Returns items with ``status=pending_intervention``, newest first.
    Scoped to the requesting tenant.
    """
    # Query all matching pending entries (no pagination yet) to get accurate total
    all_matching = governance_ledger.query(
        tenant_id=tenant_id,
        thread_id=thread_id,
        run_id=run_id,
        status="pending_intervention",
        risk_level=risk_level,
        source_agent=source_agent,
        created_from=created_from,
        created_to=created_to,
        limit=0,
        offset=0,
    )
    total = len(all_matching)
    page = all_matching[offset:offset + limit]
    items = [GovernanceItemResponse.from_entry(e, include_detail=True) for e in page]
    return GovernanceListResponse(items=items, total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# History API — resolved / rejected / failed / expired items
# ---------------------------------------------------------------------------

@router.get("/history", response_model=GovernanceListResponse)
async def list_history(
    request: Request,
    thread_id: str | None = Query(None, description="Filter by thread ID"),
    run_id: str | None = Query(None, description="Filter by run ID"),
    status: str | None = Query(None, description="Filter by status (resolved/rejected/failed/expired/decided)"),
    risk_level: str | None = Query(None, description="Filter by risk level"),
    source_agent: str | None = Query(None, description="Filter by source agent"),
    created_from: str | None = Query(None, description="Filter by created_at >= ISO datetime"),
    created_to: str | None = Query(None, description="Filter by created_at <= ISO datetime"),
    resolved_from: str | None = Query(None, description="Filter by resolved_at >= ISO datetime"),
    resolved_to: str | None = Query(None, description="Filter by resolved_at <= ISO datetime"),
    limit: int = Query(50, ge=1, le=500, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    tenant_id: str = Depends(get_tenant_id),
) -> GovernanceListResponse:
    """List resolved governance items (history view).

    By default returns all non-pending items. Use the ``status`` filter to
    narrow to a specific terminal status.

    Time range filters:
    - ``created_from`` / ``created_to`` — filter by creation time (applies to all entries)
    - ``resolved_from`` / ``resolved_to`` — filter by resolution time (only entries with resolved_at)
    """
    # Validate status filter
    if status == "pending_intervention":
        raise HTTPException(
            status_code=422,
            detail="Use /queue for pending_intervention items",
        )

    # Query all matching entries to get accurate total, then paginate
    all_matching = governance_ledger.query(
        tenant_id=tenant_id,
        thread_id=thread_id,
        run_id=run_id,
        status=status,
        risk_level=risk_level,
        source_agent=source_agent,
        created_from=created_from,
        created_to=created_to,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        limit=0,
        offset=0,
    )
    # If no status filter, exclude pending_intervention items
    if status is None:
        all_matching = [e for e in all_matching if e["status"] != "pending_intervention"]

    total = len(all_matching)
    page = all_matching[offset:offset + limit]
    items = [GovernanceItemResponse.from_entry(e) for e in page]
    return GovernanceListResponse(items=items, total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Detail API — single item
# ---------------------------------------------------------------------------

@router.get("/{governance_id}", response_model=GovernanceItemResponse)
async def get_detail(
    governance_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> GovernanceItemResponse:
    """Get full detail of a single governance item.

    Returns intervention display/action context as top-level fields for
    rendering in the operator console without additional API calls.
    Scoped to the requesting tenant.
    """
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Governance item not found: {governance_id}")
    if entry.get("tenant_id", "default") != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied: governance item belongs to another tenant")
    return GovernanceItemResponse.from_entry(entry, include_detail=True)


# ---------------------------------------------------------------------------
# Operator Action API — resolve via existing intervention contract
# ---------------------------------------------------------------------------

@router.post("/{governance_id}:resolve", response_model=OperatorResolveResponse)
async def operator_resolve(
    governance_id: str,
    body: OperatorResolveRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> OperatorResolveResponse:
    """Resolve a pending governance item via operator action.

    This endpoint is a thin wrapper around the existing intervention resolution
    contract.  It looks up the governance item, finds the associated thread and
    intervention request, validates the action, and delegates to the same
    resolution logic used by the inline intervention card.

    The operator action MUST produce the same state transition as resolving via
    the thread card — no parallel approval protocol.
    """
    # 1. Look up the governance item and verify tenant ownership
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Governance item not found: {governance_id}")
    if entry.get("tenant_id", "default") != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied: governance item belongs to another tenant")

    if entry["status"] != "pending_intervention":
        raise HTTPException(
            status_code=409,
            detail=f"Governance item is not pending — current status: {entry['status']}",
        )

    thread_id = entry["thread_id"]
    request_id = entry.get("request_id")
    if not request_id:
        raise HTTPException(
            status_code=422,
            detail="Governance item has no associated intervention request_id",
        )

    # 2. Delegate to the existing intervention resolution flow
    #    This reuses the same LangGraph client + resolution logic from interventions.py
    try:
        from langgraph_sdk import get_client

        client = get_client(url="http://127.0.0.1:2024")
        thread = await client.threads.get(thread_id)
    except Exception as e:
        logger.error("[GovernanceAPI] Failed to get thread '%s': %s", thread_id, e)
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}") from e

    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")

    try:
        thread_state = await client.threads.get_state(thread_id)
        state_values = thread_state.get("values", {}) if isinstance(thread_state, dict) else getattr(thread_state, "values", {})
    except Exception as e:
        logger.error("[GovernanceAPI] Failed to get thread state for '%s': %s", thread_id, e)
        raise HTTPException(status_code=404, detail=f"Thread state not found: {thread_id}") from e

    # Find the pending intervention task matching request_id
    task_pool = state_values.get("task_pool") or []
    pending_task = None
    for task in reversed(task_pool):
        if not isinstance(task, dict):
            continue
        intv_req = task.get("intervention_request")
        if (
            isinstance(intv_req, dict)
            and intv_req.get("request_id") == request_id
            and task.get("status") == "WAITING_INTERVENTION"
            and task.get("intervention_status") == "pending"
        ):
            pending_task = task
            break

    if pending_task is None:
        raise HTTPException(
            status_code=404,
            detail=f"No pending intervention found for request_id: {request_id}",
        )

    intervention_request = pending_task.get("intervention_request", {})

    # Use fingerprint from body or from governance metadata
    fingerprint = body.fingerprint or intervention_request.get("fingerprint")
    if not fingerprint:
        raise HTTPException(status_code=422, detail="Missing fingerprint")

    # Validate fingerprint
    if intervention_request.get("fingerprint") != fingerprint:
        raise HTTPException(status_code=409, detail="Fingerprint mismatch: intervention may be stale")

    # Validate action_key
    action_schema = intervention_request.get("action_schema", {})
    actions = action_schema.get("actions", [])
    matched_action = None
    for action in actions:
        if action.get("key") == body.action_key:
            matched_action = action
            break

    if matched_action is None:
        raise HTTPException(status_code=422, detail=f"Invalid action_key: {body.action_key}")

    # Validate payload using existing validation logic
    from src.gateway.routers.interventions import _validate_intervention_payload

    questions = intervention_request.get("questions")
    validation_action = matched_action
    if matched_action.get("kind") == "composite" and isinstance(questions, list):
        validation_action = {**matched_action, "questions": questions}

    payload_error = _validate_intervention_payload(validation_action, body.payload)
    if payload_error is not None:
        raise HTTPException(status_code=422, detail=payload_error)

    # 3. Apply resolution using the same contract as interventions.py
    from datetime import UTC, datetime

    from src.agents.intervention.decision_cache import build_cached_intervention_entry
    from src.agents.workflow_resume import apply_intervention_resolution, build_intervention_resolution_record

    now_iso = datetime.now(UTC).isoformat()
    resolution_behavior = matched_action.get("resolution_behavior", "resume_current_task")
    resolution = build_intervention_resolution_record(
        request_id=request_id,
        fingerprint=fingerprint,
        action_key=body.action_key,
        payload=body.payload,
        resolution_behavior=resolution_behavior,
    )
    updated_task, resolution_error = apply_intervention_resolution(
        pending_task,
        resolution,
        resolved_at=now_iso,
    )
    if resolution_error is not None or updated_task is None:
        raise HTTPException(status_code=422, detail=f"Failed to apply resolution: {resolution_error}")

    # Build semantic cache entry
    intervention_cache = dict(state_values.get("intervention_cache") or {})
    semantic_fp, cache_entry = build_cached_intervention_entry(
        intervention_request,
        action_key=body.action_key,
        payload=body.payload,
        resolution_behavior=resolution_behavior,
        resolved_at=now_iso,
    )
    if semantic_fp and cache_entry:
        intervention_cache[semantic_fp] = cache_entry

    # 4. Run hooks (same as interventions.py)
    _commit_values: dict[str, Any] = {
        "task_pool": [updated_task],
        "intervention_cache": intervention_cache,
    }
    try:
        from src.agents.hooks.lifecycle import apply_after_interrupt_resolve, apply_state_commit_hooks

        _commit_values = apply_after_interrupt_resolve(
            task=updated_task,
            resolution=resolution,
            source_path="governance.operator_resolve",
            proposed_update=_commit_values,
            state=state_values,
            thread_id=thread_id,
        )
        _commit_values = apply_state_commit_hooks(
            proposed_update=_commit_values,
            state=state_values,
            source_path="governance.operator_resolve",
            thread_id=thread_id,
        )
    except Exception as hook_err:
        logger.error("[GovernanceAPI] Hook error during operator resolve: %s", hook_err)
        raise HTTPException(status_code=500, detail=f"Runtime hook error: {hook_err}") from hook_err

    # 5. Persist via LangGraph state update
    try:
        await client.threads.update_state(thread_id, values=_commit_values)
    except Exception as e:
        logger.error("[GovernanceAPI] Failed to persist resolution for thread '%s': %s", thread_id, e)
        raise HTTPException(status_code=500, detail="Failed to persist resolution") from e

    # 6. Build resume hint
    resume_action_value: str | None = None
    resume_payload_value: dict[str, Any] | None = None
    if updated_task.get("status") == "RUNNING":
        resume_action_value = "submit_resume"
        resume_payload_value = {
            "message": f"[intervention_resolved] request_id={request_id} action_key={body.action_key} resolved_by=operator",
        }

    logger.info(
        "[GovernanceAPI] Operator resolved governance_id=%s request_id=%s action_key=%s",
        governance_id, request_id, body.action_key,
    )

    return OperatorResolveResponse(
        ok=True,
        governance_id=governance_id,
        status="resolved" if body.action_key != "reject" else "rejected",
        resume_action=resume_action_value,
        resume_payload=resume_payload_value,
    )
