"""Intervention resolve endpoint for the workflow intervention flow.

Frozen Phase 1 contract:
    POST /api/threads/{thread_id}/interventions/{request_id}:resolve

Request body:
    { "fingerprint": "...", "action_key": "...", "payload": {...} }

Success response:
    { "ok": true, "thread_id": "...", "request_id": "...", "fingerprint": "...", "accepted": true }

Error responses:
    404 - thread or intervention not found
    409 - fingerprint mismatch (stale)
    422 - invalid payload or action key
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/threads/{thread_id}/interventions",
    tags=["interventions"],
)


class InterventionResolveRequest(BaseModel):
    """Request body for resolving an intervention."""

    fingerprint: str
    action_key: str
    payload: dict[str, Any]


class InterventionResolveResponse(BaseModel):
    """Success response for intervention resolution."""

    ok: bool = True
    thread_id: str
    request_id: str
    fingerprint: str
    accepted: bool = True


@router.post("/{request_id}:resolve", response_model=InterventionResolveResponse)
async def resolve_intervention(
    thread_id: str,
    request_id: str,
    body: InterventionResolveRequest,
) -> InterventionResolveResponse:
    """Resolve an intervention request.

    This endpoint accepts a structured resolution for a pending intervention.
    The resolution is persisted and will be picked up when the workflow resumes.
    """
    from src.agents.thread_state import InterventionResolution
    from src.agents.workflow_resume import get_pending_intervention_task, resolve_intervention as _resolve

    # Build the resolution envelope
    resolution: InterventionResolution = {
        "request_id": request_id,
        "fingerprint": body.fingerprint,
        "action_key": body.action_key,
        "payload": body.payload,
    }

    # We need to read the current thread state from LangGraph checkpointer.
    # This requires access to the LangGraph client/store.
    try:
        from langgraph_sdk import get_client

        client = get_client(url="http://127.0.0.1:2024")
        thread = await client.threads.get(thread_id)
    except Exception as e:
        logger.error("[Intervention] Failed to get thread '%s': %s", thread_id, e)
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}") from e

    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")

    # Get the thread state (latest checkpoint values)
    try:
        thread_state = await client.threads.get_state(thread_id)
        state_values = thread_state.get("values", {}) if isinstance(thread_state, dict) else getattr(thread_state, "values", {})
    except Exception as e:
        logger.error("[Intervention] Failed to get thread state for '%s': %s", thread_id, e)
        raise HTTPException(status_code=404, detail=f"Thread state not found: {thread_id}") from e

    # Find the pending intervention task
    task_pool = state_values.get("task_pool") or []
    pending_task = None
    for task in reversed(task_pool):
        if not isinstance(task, dict):
            continue
        if task.get("status") == "WAITING_INTERVENTION" and task.get("intervention_status") == "pending":
            pending_task = task
            break

    if pending_task is None:
        raise HTTPException(status_code=404, detail=f"No pending intervention found for request_id: {request_id}")

    # Validate request_id
    intervention_request = pending_task.get("intervention_request", {})
    if not isinstance(intervention_request, dict) or intervention_request.get("request_id") != request_id:
        raise HTTPException(status_code=404, detail=f"Intervention request_id mismatch: {request_id}")

    # Validate fingerprint
    if intervention_request.get("fingerprint") != body.fingerprint:
        raise HTTPException(status_code=409, detail="Fingerprint mismatch: intervention may be stale")

    # Validate action_key exists in schema
    action_schema = intervention_request.get("action_schema", {})
    actions = action_schema.get("actions", [])
    matched_action = None
    for action in actions:
        if action.get("key") == body.action_key:
            matched_action = action
            break

    if matched_action is None:
        raise HTTPException(status_code=422, detail=f"Invalid action_key: {body.action_key}")

    # Determine resolution behavior
    resolution_behavior = matched_action.get("resolution_behavior", "resume_current_task")

    # Build the updated task
    from datetime import UTC, datetime
    now_iso = datetime.now(UTC).isoformat()

    # Build resolved inputs
    existing_resolved = dict(pending_task.get("resolved_inputs") or {})
    existing_resolved["intervention_resolution"] = {
        "action_key": body.action_key,
        "payload": body.payload,
        "resolution_behavior": resolution_behavior,
    }

    if resolution_behavior == "fail_current_task":
        new_status = "FAILED"
        status_detail = "@failed"
        error_msg = f"Intervention rejected by user: {body.action_key}"
    else:
        new_status = "RUNNING"
        status_detail = "@intervention_resolved"
        error_msg = None

    updated_task = {
        **pending_task,
        "status": new_status,
        "intervention_status": "resolved",
        "intervention_resolution": resolution,
        "resolved_inputs": existing_resolved,
        "status_detail": status_detail,
        "error": error_msg,
        "updated_at": now_iso,
    }

    # Persist the resolution by updating thread state
    try:
        await client.threads.update_state(
            thread_id,
            values={"task_pool": [updated_task]},
        )
        logger.info(
            "[Intervention] Resolution persisted for thread='%s' request_id='%s' action_key='%s' behavior='%s'",
            thread_id,
            request_id,
            body.action_key,
            resolution_behavior,
        )
    except Exception as e:
        logger.error("[Intervention] Failed to persist resolution for thread '%s': %s", thread_id, e)
        raise HTTPException(status_code=500, detail="Failed to persist intervention resolution") from e

    # If the resolution resumes the task, trigger a new run
    if new_status == "RUNNING":
        try:
            # Send a resume signal by creating a new run with the intervention answer
            from langchain_core.messages import HumanMessage
            resume_message = f"[intervention_resolved] request_id={request_id} action_key={body.action_key}"
            await client.runs.create(
                thread_id,
                assistant_id="entry_graph",
                input={"messages": [{"role": "human", "content": resume_message}]},
            )
            logger.info("[Intervention] Resume run created for thread='%s'", thread_id)
        except Exception as e:
            # Resolution is persisted even if resume fails; the user can retry
            logger.warning("[Intervention] Failed to create resume run for thread '%s': %s", thread_id, e)

    return InterventionResolveResponse(
        ok=True,
        thread_id=thread_id,
        request_id=request_id,
        fingerprint=body.fingerprint,
        accepted=True,
    )
