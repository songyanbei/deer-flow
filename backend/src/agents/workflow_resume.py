from __future__ import annotations

import json
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
    # ── Intervention resolution path (source of truth) ──
    # When a task resumes after a user_clarification intervention, extract
    # the answer from the persisted intervention_resolution rather than
    # relying on message-based heuristics.
    task_pool = state.get("task_pool") or []
    for task in reversed(task_pool):
        if not isinstance(task, dict):
            continue
        if task.get("continuation_mode") == "continue_after_intervention":
            answer = normalize_intervention_clarification_answer(task)
            if answer:
                return answer
            # Redundant fallback: try message-based extraction
            answer = extract_intervention_clarification_from_message(state)
            if answer:
                return answer
            break

    # ── Original paths (structured answers / message heuristic) ──
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


# ---------------------------------------------------------------------------
# Redundant message-based fallback (方案三)
# ---------------------------------------------------------------------------


def extract_intervention_clarification_from_message(state: ThreadState) -> str:
    """Extract a user answer from an ``[intervention_resolved]`` resume message.

    The **primary** source of truth is always
    ``task["intervention_resolution"]["payload"]`` (handled by
    :func:`normalize_intervention_clarification_answer`).  This function
    serves only as a **redundant fallback** for edge cases where the primary
    path fails — for example old task formats missing ``intervention_resolution``.

    It does NOT rely on Chinese keywords or natural-language heuristics.
    It attempts to parse a JSON object from the message remainder; plain-text
    content after the prefix is intentionally ignored to avoid false positives.
    """
    messages = state.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    if not is_human_message(last):
        return ""
    text = content_to_text(getattr(last, "content", "")).strip()
    if not text.startswith("[intervention_resolved]"):
        return ""
    remainder = text[len("[intervention_resolved]"):].strip()
    if not remainder:
        return ""
    # Only parse structured JSON — do not treat arbitrary plain text as answer
    try:
        parsed = json.loads(remainder)
        if isinstance(parsed, dict):
            return str(parsed.get("answer") or parsed.get("text") or "").strip()
    except (json.JSONDecodeError, ValueError):
        pass
    return ""


# ---------------------------------------------------------------------------
# Intervention clarification answer normalization
# ---------------------------------------------------------------------------


def _extract_value_by_kind(kind: str, payload: Mapping[str, Any]) -> str:
    """Extract a human-readable answer string from a resolution payload entry
    according to the action/question *kind*.

    This function is schema-driven — it inspects well-known payload keys
    (``text``, ``selected``, ``comment``, ``custom_text``) instead of
    relying on natural-language heuristics or agent-specific patterns.
    """
    if kind == "input":
        text = str(payload.get("text") or "").strip()
        if text:
            return text
        return str(payload.get("comment") or "").strip()

    if kind == "confirm":
        return "confirmed"

    if kind in ("single_select", "select"):
        selected = str(payload.get("selected") or "").strip()
        custom = str(payload.get("custom_text") or "").strip()
        parts = [p for p in (selected, custom) if p]
        return ", ".join(parts)

    if kind == "multi_select":
        raw = payload.get("selected")
        if isinstance(raw, list):
            items = [str(item).strip() for item in raw if str(item).strip()]
            return ", ".join(items)
        return str(raw or "").strip()

    if kind == "button":
        return ""

    # Unknown kind — JSON fallback
    try:
        return json.dumps(dict(payload), ensure_ascii=False)
    except Exception:
        return str(payload)


def normalize_intervention_clarification_answer(task: TaskStatus) -> str:
    """Extract a human-readable user answer from a resolved user_clarification
    intervention stored on *task*.

    Returns an empty string when the task does not carry a user_clarification
    intervention or when the resolution cannot be meaningfully extracted.

    This is the **single authoritative extractor** for intervention-based
    clarification answers.  It operates on the persisted
    ``intervention_request`` / ``intervention_resolution`` fields — never on
    message text or Chinese keyword matching.
    """
    if task.get("continuation_mode") != "continue_after_intervention":
        return ""

    intervention_request = task.get("intervention_request")
    if not isinstance(intervention_request, dict):
        return ""

    # Only handle user_clarification interventions — NOT before_tool confirmations
    if intervention_request.get("category") != "user_clarification":
        return ""

    resolution = task.get("intervention_resolution")
    if not isinstance(resolution, dict):
        return ""

    if resolution.get("resolution_behavior") != "resume_current_task":
        return ""

    payload = resolution.get("payload")
    if not isinstance(payload, dict) or not payload:
        return ""

    action_key = resolution.get("action_key", "")

    # Find the matching action to determine its kind
    action_schema = intervention_request.get("action_schema")
    actions = (action_schema.get("actions") or []) if isinstance(action_schema, dict) else []
    matched_action: dict[str, Any] | None = None
    for action in actions:
        if isinstance(action, dict) and action.get("key") == action_key:
            matched_action = action
            break

    if matched_action is None:
        # No matching action — try generic extraction
        return _extract_value_by_kind("input", payload)

    kind = str(matched_action.get("kind") or "input")

    # Composite: expand each sub-question answer with its label
    if kind == "composite":
        questions = intervention_request.get("questions") or []
        if not questions:
            return _extract_value_by_kind("input", payload)

        lines: list[str] = []
        for question in questions:
            if not isinstance(question, dict):
                continue
            q_key = str(question.get("key") or "").strip()
            q_label = str(question.get("label") or q_key).strip()
            q_kind = str(question.get("kind") or "input")
            q_value = payload.get(q_key)
            if isinstance(q_value, dict):
                answer_text = _extract_value_by_kind(q_kind, q_value)
            elif isinstance(q_value, str):
                answer_text = q_value.strip()
            elif q_value is not None:
                answer_text = str(q_value).strip()
            else:
                continue
            if answer_text:
                lines.append(f"{q_label}: {answer_text}")
        return "\n".join(lines)

    return _extract_value_by_kind(kind, payload)
