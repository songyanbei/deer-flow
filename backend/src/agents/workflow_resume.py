from __future__ import annotations

from collections.abc import Mapping
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


def _extract_resume_payload(config: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(config, Mapping):
        return None

    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        return configurable

    context = config.get("context")
    if isinstance(context, Mapping):
        return context

    return None


def extract_structured_clarification_answers(
    state: ThreadState,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    payload = _extract_resume_payload(config)
    if not isinstance(payload, Mapping):
        return {}

    raw_response = payload.get("workflow_clarification_response")
    if not isinstance(raw_response, Mapping):
        raw_response = payload.get("clarification_response")
    if not isinstance(raw_response, Mapping):
        return {}

    raw_answers = raw_response.get("answers")
    if not isinstance(raw_answers, Mapping):
        return {}

    task = _latest_waiting_clarification_task(state)
    question_keys: set[str] = set()
    clarification_request = task.get("clarification_request") if isinstance(task, dict) else None
    if isinstance(clarification_request, Mapping):
        questions = clarification_request.get("questions")
        if isinstance(questions, list):
            for question in questions:
                if not isinstance(question, Mapping):
                    continue
                key = str(question.get("key") or "").strip()
                if key:
                    question_keys.add(key)

    answers: dict[str, str] = {}
    for key, value in raw_answers.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        if question_keys and normalized_key not in question_keys:
            continue

        text = ""
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, Mapping):
            candidate = value.get("text")
            if isinstance(candidate, str) and candidate.strip():
                text = candidate.strip()
        else:
            text = str(value).strip()

        if text:
            answers[normalized_key] = text

    return answers


def _format_structured_clarification_answers(
    state: ThreadState,
    answers: Mapping[str, str],
) -> str:
    if not answers:
        return ""

    task = _latest_waiting_clarification_task(state)
    label_map: dict[str, str] = {}
    clarification_request = task.get("clarification_request") if isinstance(task, dict) else None
    if isinstance(clarification_request, Mapping):
        questions = clarification_request.get("questions")
        if isinstance(questions, list):
            for question in questions:
                if not isinstance(question, Mapping):
                    continue
                key = str(question.get("key") or "").strip()
                label = str(question.get("label") or "").strip()
                if key and label:
                    label_map[key] = label

    lines: list[str] = []
    for key, value in answers.items():
        label = label_map.get(key, key)
        lines.append(f"{label} {value}".strip())
    return "\n".join(lines)


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
    # Phase 2: prefer continuation_mode if present
    task_pool = state.get("task_pool") or []
    for task in reversed(task_pool):
        if not isinstance(task, dict):
            continue
        if task.get("continuation_mode") == "continue_after_clarification":
            return True
    # Legacy fallback
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


def extract_latest_clarification_answer(
    state: ThreadState,
    config: Mapping[str, Any] | None = None,
) -> str:
    structured_answers = extract_structured_clarification_answers(state, config)
    if structured_answers:
        return _format_structured_clarification_answers(state, structured_answers)

    if not latest_user_message_is_clarification_answer(state):
        return ""
    messages = state.get("messages") or []
    last = messages[-1]
    answer = content_to_text(getattr(last, "content", ""))
    # Intervention resume messages reuse the clarification-resume control flow
    # to keep the same workflow run id, but they are not user clarification
    # content and should not be injected into the executor prompt.
    if answer.strip().startswith("[intervention_resolved]"):
        return ""
    return answer


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
    if task is None:
        return False
    # Phase 2: prefer continuation_mode if present
    if task.get("continuation_mode") == "resume_tool_call":
        return task.get("intervention_status") == "pending"
    # Legacy fallback
    return task.get("intervention_status") == "pending"


def get_pending_intervention_task(state: ThreadState) -> TaskStatus | None:
    """Return the task with a pending intervention, if any."""
    task = _latest_waiting_intervention_task(state)
    if task is not None and task.get("intervention_status") == "pending":
        return task
    return None


def build_intervention_resolution_record(
    *,
    request_id: str,
    fingerprint: str,
    action_key: str,
    payload: Mapping[str, Any] | None,
    resolution_behavior: str,
) -> InterventionResolution:
    return {
        "request_id": request_id,
        "fingerprint": fingerprint,
        "action_key": action_key,
        "payload": dict(payload or {}),
        "resolution_behavior": resolution_behavior,
    }


def build_intervention_resolved_inputs_entry(
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "request_id": str(resolution.get("request_id") or ""),
        "fingerprint": str(resolution.get("fingerprint") or ""),
        "action_key": str(resolution.get("action_key") or ""),
        "payload": dict(resolution.get("payload") or {}),
        "resolution_behavior": str(resolution.get("resolution_behavior") or "resume_current_task"),
    }


def resolve_intervention_behavior(
    intervention_request: Mapping[str, Any] | None,
    action_key: str,
) -> str | None:
    if not isinstance(intervention_request, Mapping):
        return None
    action_schema = intervention_request.get("action_schema", {})
    actions = action_schema.get("actions", []) if isinstance(action_schema, Mapping) else []
    for action in actions:
        if isinstance(action, Mapping) and action.get("key") == action_key:
            return str(action.get("resolution_behavior") or "resume_current_task")
    return None


def apply_intervention_resolution(
    task: TaskStatus,
    resolution: InterventionResolution,
    *,
    resolved_at: str | None = None,
) -> tuple[TaskStatus | None, str | None]:
    intervention_request = task.get("intervention_request")
    if not isinstance(intervention_request, dict):
        return None, "missing_intervention_request"

    if resolution["fingerprint"] != intervention_request.get("fingerprint"):
        return None, "fingerprint_mismatch"

    if resolution["request_id"] != intervention_request.get("request_id"):
        return None, "request_id_mismatch"

    resolution_behavior = resolve_intervention_behavior(intervention_request, resolution["action_key"])
    if resolution_behavior is None:
        return None, "invalid_action_key"

    normalized_resolution = build_intervention_resolution_record(
        request_id=resolution["request_id"],
        fingerprint=resolution["fingerprint"],
        action_key=resolution["action_key"],
        payload=resolution.get("payload", {}),
        resolution_behavior=resolution_behavior,
    )

    if resolution_behavior == "resume_current_task":
        new_status = "RUNNING"
    elif resolution_behavior == "fail_current_task":
        new_status = "FAILED"
    elif resolution_behavior == "replan_from_resolution":
        new_status = "RUNNING"
    else:
        return None, f"unknown_resolution_behavior:{resolution_behavior}"

    existing_resolved = dict(task.get("resolved_inputs") or {})
    existing_resolved["intervention_resolution"] = build_intervention_resolved_inputs_entry(normalized_resolution)

    updated_task: TaskStatus = {
        **task,
        "status": new_status,
        "intervention_status": "resolved",
        "intervention_resolution": normalized_resolution,
        "resolved_inputs": existing_resolved,
        "status_detail": "@intervention_resolved" if new_status == "RUNNING" else "@failed",
        "error": f"Intervention rejected by user: {resolution['action_key']}" if new_status == "FAILED" else None,
    }
    if resolved_at:
        updated_task["updated_at"] = resolved_at

    return updated_task, None


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
    return apply_intervention_resolution(task, resolution)
