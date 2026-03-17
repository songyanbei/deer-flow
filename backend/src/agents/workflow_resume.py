from __future__ import annotations

from typing import Any

from src.agents.thread_state import InterventionResolution, TaskStatus, ThreadState

_EXPLICIT_NEW_REQUEST_MARKERS = (
    "actually",
    "instead",
    "ignore that",
    "ignore the previous",
    "ignore the last",
    "new request",
    "another task",
    "start over",
    "restart",
    "switch to",
    "different question",
    "forget that",
    "另外",
    "改为",
    "改成",
    "换成",
    "换个",
    "忽略上面",
    "忽略之前",
    "别管",
    "先不管",
    "重新开始",
    "重新帮我",
    "新任务",
    "另一个问题",
)


def content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def is_human_message(message: object) -> bool:
    role = getattr(message, "type", None)
    return role == "human" or message.__class__.__name__ == "HumanMessage"


def is_clarification_message(message: object) -> bool:
    if getattr(message, "name", None) == "ask_clarification":
        return True
    role = getattr(message, "type", None)
    return role == "tool" and getattr(message, "name", None) == "ask_clarification"


def extract_latest_user_input(state: ThreadState) -> str:
    for message in reversed(state.get("messages") or []):
        if is_human_message(message):
            return content_to_text(getattr(message, "content", ""))
    return ""


def _latest_waiting_clarification_task(state: ThreadState) -> TaskStatus | None:
    task_pool = state.get("task_pool") or []
    for task in reversed(task_pool):
        if not isinstance(task, dict):
            continue
        if task.get("status") != "RUNNING":
            continue
        prompt = task.get("clarification_prompt")
        if isinstance(prompt, str) and prompt.strip():
            return task
    return None


def workflow_has_pending_clarification(state: ThreadState) -> bool:
    task = _latest_waiting_clarification_task(state)
    if task is None:
        return False
    return state.get("execution_state") == "INTERRUPTED" or task.get("status") == "RUNNING"


def _looks_like_explicit_new_request(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    return any(marker in lowered or marker in text for marker in _EXPLICIT_NEW_REQUEST_MARKERS)


def looks_like_explicit_new_request(text: str) -> bool:
    return _looks_like_explicit_new_request(text)


def is_intervention_resolution_message(message: object) -> bool:
    """Check if a message is an intervention resolution resume message."""
    if not is_human_message(message):
        return False
    text = content_to_text(getattr(message, "content", ""))
    return text.strip().startswith("[intervention_resolved]")


def latest_user_message_is_clarification_answer(state: ThreadState) -> bool:
    messages = state.get("messages") or []
    if not messages:
        return False

    last = messages[-1]
    if not is_human_message(last):
        return False

    latest_input = content_to_text(getattr(last, "content", "")).strip()
    if not latest_input:
        return False

    # Intervention resolution messages are not clarification answers
    if latest_input.startswith("[intervention_resolved]"):
        return True  # Treat as clarification-like resume (skip new request detection)

    if _looks_like_explicit_new_request(latest_input):
        return False

    if len(messages) >= 2 and is_clarification_message(messages[-2]):
        return True

    return workflow_has_pending_clarification(state)


def extract_latest_clarification_answer(state: ThreadState) -> str:
    if not latest_user_message_is_clarification_answer(state):
        return ""
    messages = state.get("messages") or []
    last = messages[-1]
    return content_to_text(getattr(last, "content", ""))


# ---------------------------------------------------------------------------
# Intervention resolution helpers
# ---------------------------------------------------------------------------


def _latest_waiting_intervention_task(state: ThreadState) -> TaskStatus | None:
    """Find the most recent task in WAITING_INTERVENTION state."""
    task_pool = state.get("task_pool") or []
    for task in reversed(task_pool):
        if not isinstance(task, dict):
            continue
        if task.get("status") == "WAITING_INTERVENTION":
            return task
    return None


def workflow_has_pending_intervention(state: ThreadState) -> bool:
    """Check if the workflow has a pending intervention."""
    task = _latest_waiting_intervention_task(state)
    return task is not None and task.get("intervention_status") == "pending"


def get_pending_intervention_task(state: ThreadState) -> TaskStatus | None:
    """Return the task with a pending intervention, if any."""
    task = _latest_waiting_intervention_task(state)
    if task is not None and task.get("intervention_status") == "pending":
        return task
    return None


def resolve_intervention(
    state: ThreadState,
    resolution: InterventionResolution,
) -> tuple[TaskStatus | None, str | None]:
    """Validate and apply an intervention resolution.

    Returns:
        (updated_task, error_message)
        If error_message is not None, the resolution was rejected.
    """
    task = _latest_waiting_intervention_task(state)
    if task is None:
        return None, "no_pending_intervention"

    intervention_request = task.get("intervention_request")
    if not isinstance(intervention_request, dict):
        return None, "missing_intervention_request"

    # Fingerprint check
    if resolution["fingerprint"] != intervention_request.get("fingerprint"):
        return None, "fingerprint_mismatch"

    # Request ID check
    if resolution["request_id"] != intervention_request.get("request_id"):
        return None, "request_id_mismatch"

    # Find the matching action in the schema
    action_schema = intervention_request.get("action_schema", {})
    actions = action_schema.get("actions", [])
    matched_action = None
    for action in actions:
        if action.get("key") == resolution["action_key"]:
            matched_action = action
            break

    if matched_action is None:
        return None, "invalid_action_key"

    # Determine resolution behavior
    resolution_behavior = matched_action.get("resolution_behavior", "resume_current_task")

    if resolution_behavior == "resume_current_task":
        new_status = "RUNNING"
    elif resolution_behavior == "fail_current_task":
        new_status = "FAILED"
    elif resolution_behavior == "replan_from_resolution":
        # Phase 1: protocol reserved, treat as resume for now
        new_status = "RUNNING"
    else:
        return None, f"unknown_resolution_behavior:{resolution_behavior}"

    # Build user payload as resolved_inputs so the resumed agent can access it
    user_payload = resolution.get("payload", {})
    existing_resolved = dict(task.get("resolved_inputs") or {})
    existing_resolved["intervention_resolution"] = {
        "action_key": resolution["action_key"],
        "payload": user_payload,
        "resolution_behavior": resolution_behavior,
    }

    updated_task: TaskStatus = {
        **task,
        "status": new_status,
        "intervention_status": "resolved",
        "intervention_resolution": resolution,
        "resolved_inputs": existing_resolved,
        "status_detail": "@intervention_resolved" if new_status == "RUNNING" else "@failed",
        "error": f"Intervention rejected by user: {resolution['action_key']}" if new_status == "FAILED" else None,
    }

    return updated_task, None
