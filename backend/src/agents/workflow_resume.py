from __future__ import annotations

from typing import Any

from src.agents.thread_state import TaskStatus, ThreadState

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


def latest_user_message_is_clarification_answer(state: ThreadState) -> bool:
    messages = state.get("messages") or []
    if len(messages) < 2:
        return False

    last = messages[-1]
    prev = messages[-2]
    if not is_human_message(last):
        return False
    if not is_clarification_message(prev):
        return False

    if not workflow_has_pending_clarification(state):
        task_pool = state.get("task_pool") or []
        if not task_pool:
            return False

    latest_input = content_to_text(getattr(last, "content", "")).strip()
    if not latest_input:
        return False

    return not _looks_like_explicit_new_request(latest_input)


def extract_latest_clarification_answer(state: ThreadState) -> str:
    if not latest_user_message_is_clarification_answer(state):
        return ""
    messages = state.get("messages") or []
    last = messages[-1]
    return content_to_text(getattr(last, "content", ""))
