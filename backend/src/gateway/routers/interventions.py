"""Intervention resolve endpoint for the workflow intervention flow.

Frozen Phase 1 contract:
    POST /api/threads/{thread_id}/interventions/{request_id}:resolve

Request body:
    { "fingerprint": "...", "action_key": "...", "payload": {...} }

Success response:
    { "ok": true, "thread_id": "...", "request_id": "...", "fingerprint": "...", "accepted": true,
      "resume_action": "submit_resume" | null, "resume_payload": { "message": "..." } | null }

Error responses:
    404 - thread or intervention not found
    409 - fingerprint mismatch (stale)
    422 - invalid payload or action key
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.agents.intervention.decision_cache import build_cached_intervention_entry
from src.gateway.dependencies import get_tenant_id, get_user_id
from src.gateway.thread_registry import get_thread_registry

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
    resume_action: str | None = None
    resume_payload: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None


def _normalize_select_options(action: dict[str, Any]) -> set[str]:
    options = action.get("options")
    if not isinstance(options, list):
        return set()
    values: set[str] = set()
    for option in options:
        value = option.get("value") if isinstance(option, dict) else option
        if value is None:
            continue
        text = str(value).strip()
        if text:
            values.add(text)
    return values


def _validate_question_payload(question: dict[str, Any], payload: dict[str, Any]) -> str | None:
    return _validate_intervention_payload(question, payload)


def _validate_intervention_payload(action: dict[str, Any], payload: dict[str, Any]) -> str | None:
    kind = str(action.get("kind") or "").strip()
    required = action.get("required")
    if required is None:
        required = kind in {"confirm", "input", "single_select", "multi_select"}

    if kind == "confirm":
        if payload.get("confirmed") is not True:
            return "Confirm payload must include confirmed=true"
        return None

    if kind == "input":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            text = payload.get("comment")
        if required and (not isinstance(text, str) or not text.strip()):
            return "Input payload must include non-empty text"
        return None

    if kind in {"select", "single_select"}:
        selected = payload.get("selected")
        if not isinstance(selected, str) or not selected.strip():
            return "Single-select payload must include a selected value"
        if payload.get("custom") is True:
            custom_text = payload.get("custom_text")
            if not isinstance(custom_text, str) or not custom_text.strip():
                return "Custom single-select payload must include custom_text"
            return None
        allowed = _normalize_select_options(action)
        if allowed and selected not in allowed:
            return "Selected value is not in the allowed options"
        return None

    if kind == "multi_select":
        selected = payload.get("selected")
        if not isinstance(selected, list) or not all(isinstance(item, str) and item.strip() for item in selected):
            return "Multi-select payload must include a non-empty selected array"
        normalized = [item.strip() for item in selected if item.strip()]
        if required and not normalized:
            return "Multi-select payload must include at least one selected value"
        if payload.get("custom") is True:
            custom_values = payload.get("custom_values")
            if custom_values is not None and (
                not isinstance(custom_values, list)
                or not all(isinstance(item, str) and item.strip() for item in custom_values)
            ):
                return "Custom multi-select payload must include non-empty custom_values"
            custom_text = payload.get("custom_text")
            if custom_values is None and (
                not isinstance(custom_text, str) or not custom_text.strip()
            ):
                return "Custom multi-select payload must include custom_text or custom_values"
        allowed = _normalize_select_options(action)
        if allowed and any(
            item not in allowed
            for item in normalized
            if item not in (payload.get("custom_values") or [])
        ):
            return "Selected values must come from the allowed options"
        min_select = action.get("min_select")
        max_select = action.get("max_select")
        if isinstance(min_select, int) and len(normalized) < min_select:
            return f"Please select at least {min_select} option(s)"
        if isinstance(max_select, int) and len(normalized) > max_select:
            return f"Please select no more than {max_select} option(s)"
        return None

    if kind == "composite":
        answers = payload.get("answers")
        if not isinstance(answers, dict) or not answers:
            return "Composite payload must include non-empty answers"
        questions = action.get("questions")
        if not isinstance(questions, list) or not questions:
            return None
        for question in questions:
            if not isinstance(question, dict):
                continue
            question_key = str(question.get("key") or "").strip()
            if not question_key:
                continue
            answer_payload = answers.get(question_key)
            if not isinstance(answer_payload, dict):
                return f"Missing answer payload for question: {question_key}"
            question_error = _validate_question_payload(question, answer_payload)
            if question_error is not None:
                return f"{question_key}: {question_error}"
        return None

    return None


@router.post("/{request_id}:resolve", response_model=InterventionResolveResponse)
async def resolve_intervention(
    thread_id: str,
    request_id: str,
    body: InterventionResolveRequest,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
) -> InterventionResolveResponse:
    """Resolve an intervention request.

    This endpoint accepts a structured resolution for a pending intervention.
    The resolution is persisted and will be picked up when the workflow resumes.
    """
    # Tenant + user access control
    if not get_thread_registry().check_access(thread_id, tenant_id, user_id):
        raise HTTPException(status_code=403, detail="Access denied")

    from src.agents.workflow_resume import apply_intervention_resolution, build_intervention_resolution_record

    # Build the resolution envelope
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

    questions = intervention_request.get("questions")
    validation_action = matched_action
    if matched_action.get("kind") == "composite" and isinstance(questions, list):
        validation_action = {
            **matched_action,
            "questions": questions,
        }

    payload_error = _validate_intervention_payload(validation_action, body.payload)
    if payload_error is not None:
        raise HTTPException(status_code=422, detail=payload_error)

    from datetime import UTC, datetime
    now_iso = datetime.now(UTC).isoformat()
    resolution_behavior = matched_action.get("resolution_behavior", "resume_current_task")
    resolution = build_intervention_resolution_record(
        request_id=request_id,
        fingerprint=body.fingerprint,
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
        raise HTTPException(status_code=422, detail=f"Failed to apply intervention resolution: {resolution_error}")
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
        logger.info(
            "[Intervention] [Cache WRITE] semantic_fp=%s type=%s max_reuse=%s",
            semantic_fp,
            cache_entry.get("intervention_type"),
            cache_entry.get("max_reuse"),
        )

    # --- Slice B hooks: after_interrupt_resolve + state commit ---
    _commit_values: dict[str, Any] = {
        "task_pool": [updated_task],
        "intervention_cache": intervention_cache,
    }
    try:
        from src.agents.hooks.lifecycle import apply_after_interrupt_resolve, apply_state_commit_hooks
        _commit_values = apply_after_interrupt_resolve(
            task=updated_task,
            resolution=resolution,
            source_path="gateway.resolve_intervention",
            proposed_update=_commit_values,
            state=state_values,
            thread_id=thread_id,
        )
        _commit_values = apply_state_commit_hooks(
            proposed_update=_commit_values,
            state=state_values,
            source_path="gateway.resolve_intervention",
            thread_id=thread_id,
        )
    except Exception as hook_err:
        logger.error("[Intervention] Hook error during resolve for thread '%s': %s", thread_id, hook_err)
        raise HTTPException(status_code=500, detail=f"Runtime hook error: {hook_err}") from hook_err

    # Persist the resolution by updating thread state
    checkpoint_value: dict[str, Any] | None = None
    try:
        update_response = await client.threads.update_state(
            thread_id,
            values=_commit_values,
        )
        if isinstance(update_response, dict):
            checkpoint_value = update_response.get("checkpoint")
        else:
            checkpoint_value = getattr(update_response, "checkpoint", None)
        logger.info(
            "[Intervention] interrupt_resolution_persisted thread='%s' request_id='%s' action_key='%s' behavior='%s' checkpoint=%s",
            thread_id,
            request_id,
            body.action_key,
            resolution_behavior,
            checkpoint_value,
        )
    except Exception as e:
        logger.error("[Intervention] Failed to persist resolution for thread '%s': %s", thread_id, e)
        raise HTTPException(status_code=500, detail="Failed to persist intervention resolution") from e

    # Build resume hint for the frontend.
    # The frontend is responsible for submitting the resume run via its own
    # streaming connection (useStream.submit) so that SSE events are observable.
    # The backend only persists the resolution; creating a background run via
    # client.runs.create() would be invisible to the frontend's SSE stream.
    resume_action_value: str | None = None
    resume_payload_value: dict[str, Any] | None = None
    if updated_task["status"] == "RUNNING":
        resume_message = f"[intervention_resolved] request_id={request_id} action_key={body.action_key}"
        resume_action_value = "submit_resume"
        resume_payload_value = {
            "message": resume_message,
        }
        logger.info(
            "[Intervention] Resolution persisted and resume hint prepared. "
            "thread='%s' request_id='%s' task_id='%s' run_status='%s' resume_action='%s' resume_message=%r",
            thread_id,
            request_id,
            updated_task.get("task_id"),
            updated_task.get("status"),
            resume_action_value,
            resume_message,
        )

    return InterventionResolveResponse(
        ok=True,
        thread_id=thread_id,
        request_id=request_id,
        fingerprint=body.fingerprint,
        accepted=True,
        resume_action=resume_action_value,
        resume_payload=resume_payload_value,
        checkpoint=checkpoint_value,
    )
