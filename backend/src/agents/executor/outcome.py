"""Structured outcome types for workflow executor classification.

This module provides a normalized AgentOutcome discriminated union and a
normalize_agent_outcome() function that inspects only the *current round*
of agent messages to determine how the executor should branch.

The key invariant is that only messages at or after ``new_messages_start``
are used for classification.  Older replayed history is never treated as the
current round's result.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, ToolMessage

from src.agents.thread_state import TaskStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


class ToolIntent(TypedDict):
    """Structured representation of a pending tool call."""

    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: NotRequired[str | None]
    idempotency_key: NotRequired[str | None]
    source_agent: NotRequired[str | None]
    source_task_id: NotRequired[str | None]


class _OutcomeBase(TypedDict):
    kind: str
    messages: list[Any]
    new_messages_start: int


class CompleteOutcome(_OutcomeBase):
    kind: Literal["complete"]
    result_text: str
    fact_payload: dict[str, Any]


class RequestDependencyOutcome(_OutcomeBase):
    kind: Literal["request_dependency"]
    help_request: dict[str, Any]


class RequestClarificationOutcome(_OutcomeBase):
    kind: Literal["request_clarification"]
    prompt: str


class RequestInterventionOutcome(_OutcomeBase):
    kind: Literal["request_intervention"]
    intervention_request: dict[str, Any]
    pending_tool_call: ToolIntent | None


class FailOutcome(_OutcomeBase):
    kind: Literal["fail"]
    error_message: str
    retryable: bool


AgentOutcome = (
    CompleteOutcome
    | RequestDependencyOutcome
    | RequestClarificationOutcome
    | RequestInterventionOutcome
    | FailOutcome
)

# ---------------------------------------------------------------------------
# Terminal tool names recognised by the normalizer
# ---------------------------------------------------------------------------

_TERMINAL_TOOL_NAMES = frozenset(
    {
        "intervention_required",
        "request_help",
        "ask_clarification",
        "task_complete",
        "task_fail",
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_agent_output(messages: list[Any]) -> str:
    """Return the text of the last non-ToolMessage AIMessage."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        if not isinstance(message, AIMessage):
            continue
        text = _content_to_text(getattr(message, "content", ""))
        if text.strip():
            return text.strip()
    return ""


def _extract_intercepted_tool_call_from_messages(messages: list[Any], intervention_tool_call_id: str | None) -> ToolIntent | None:
    """Find the AIMessage tool_call that produced the intervention_required ToolMessage."""
    if not intervention_tool_call_id:
        return None
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        for tc in msg.tool_calls or []:
            if tc.get("id") == intervention_tool_call_id:
                return ToolIntent(
                    tool_name=tc["name"],
                    tool_args=tc.get("args", {}),
                    tool_call_id=tc["id"],
                )
    return None


def _find_last_terminal_in_range(messages: list[Any], start: int) -> tuple[int, ToolMessage] | None:
    """Scan messages[start:] backwards for the last terminal tool signal."""
    for idx in range(len(messages) - 1, start - 1, -1):
        msg = messages[idx]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", None) in _TERMINAL_TOOL_NAMES:
            return idx, msg
    return None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_agent_outcome(
    *,
    task: TaskStatus,
    messages: list[Any],
    new_messages_start: int,
) -> tuple[AgentOutcome, bool]:
    """Classify the current execution round into a structured outcome.

    Only messages at ``messages[new_messages_start:]`` are inspected for
    terminal tool signals.  If no structured signal is found, legacy
    fallback heuristics are used.

    Returns:
        A tuple of ``(outcome, used_fallback)`` where *used_fallback* is
        ``True`` when the classification relied on legacy heuristics rather
        than an explicit tool signal.
    """
    current_round = messages[new_messages_start:]

    # --- Priority 1: find explicit terminal tool in current round ---
    terminal = _find_last_terminal_in_range(messages, new_messages_start)

    if terminal is not None:
        terminal_idx, terminal_msg = terminal
        tool_name = getattr(terminal_msg, "name", None)
        raw_content = _content_to_text(terminal_msg.content)

        # 1. intervention_required
        if tool_name == "intervention_required":
            payload = _parse_json_object(raw_content)
            pending_tool = None
            if payload:
                tcid = getattr(terminal_msg, "tool_call_id", None)
                pending_tool = _extract_intercepted_tool_call_from_messages(messages, tcid)
            return RequestInterventionOutcome(
                kind="request_intervention",
                messages=messages,
                new_messages_start=new_messages_start,
                intervention_request=payload or {},
                pending_tool_call=pending_tool,
            ), False

        # 2. request_help
        if tool_name == "request_help":
            payload = _parse_json_object(raw_content)
            return RequestDependencyOutcome(
                kind="request_dependency",
                messages=messages,
                new_messages_start=new_messages_start,
                help_request=payload or {},
            ), False

        # 3. ask_clarification
        if tool_name == "ask_clarification":
            return RequestClarificationOutcome(
                kind="request_clarification",
                messages=messages,
                new_messages_start=new_messages_start,
                prompt=raw_content,
            ), False

        # 4. task_complete
        if tool_name == "task_complete":
            payload = _parse_json_object(raw_content) or {}
            result_text = payload.get("result_text", "")
            if not result_text:
                # Fall back to agent output text if tool payload is empty
                result_text = _extract_agent_output(current_round) or raw_content
            fact_payload = payload.get("fact_payload") or {}
            if not fact_payload and result_text:
                parsed = _parse_json_object(result_text)
                fact_payload = parsed if parsed is not None else {"text": result_text}
            return CompleteOutcome(
                kind="complete",
                messages=messages,
                new_messages_start=new_messages_start,
                result_text=result_text,
                fact_payload=fact_payload,
            ), False

        # 5. task_fail
        if tool_name == "task_fail":
            payload = _parse_json_object(raw_content) or {}
            return FailOutcome(
                kind="fail",
                messages=messages,
                new_messages_start=new_messages_start,
                error_message=payload.get("error_message", raw_content),
                retryable=bool(payload.get("retryable", False)),
            ), False

    # --- Priority 2: legacy fallback (current-round text only) ---
    agent_output = _extract_agent_output(current_round)

    if not agent_output:
        # No output at all — treat as failure
        return FailOutcome(
            kind="fail",
            messages=messages,
            new_messages_start=new_messages_start,
            error_message="Domain agent returned no final answer.",
            retryable=False,
        ), True

    # Legacy implicit clarification detection (imported from executor)
    if _looks_like_implicit_clarification(agent_output):
        return RequestClarificationOutcome(
            kind="request_clarification",
            messages=messages,
            new_messages_start=new_messages_start,
            prompt=agent_output,
        ), True

    # Default: treat final AI text as completion
    fact_payload = _parse_json_object(agent_output)
    if fact_payload is None:
        fact_payload = {"text": agent_output}

    return CompleteOutcome(
        kind="complete",
        messages=messages,
        new_messages_start=new_messages_start,
        result_text=agent_output,
        fact_payload=fact_payload,
    ), True


# ---------------------------------------------------------------------------
# Legacy implicit clarification heuristic (moved here for reuse)
# ---------------------------------------------------------------------------

_IMPLICIT_CLARIFICATION_MARKERS = (
    "请选择",
    "请确认",
    "请提供",
    "请补充",
    "请告知",
    "please choose",
    "please confirm",
    "please provide",
    "which",
)
_COMPLETION_TEXT_MARKERS = (
    "已预定",
    "预定成功",
    "已预约",
    "booked",
    "confirmed",
    "created successfully",
)
_NUMBERED_OPTION_PATTERN = re.compile(r"(?m)^\s*\d+[\.\)]\s+\S+")


def _contains_choice_enumeration(text: str) -> bool:
    lowered = text.lower()
    if len(_NUMBERED_OPTION_PATTERN.findall(text)) >= 2:
        return True
    if "或" in text and any(separator in text for separator in ("、", "，", ",")):
        return True
    return " or " in lowered and "," in lowered


def _looks_like_implicit_clarification(agent_output: str) -> bool:
    text = agent_output.strip()
    if not text:
        return False
    if _parse_json_object(text) is not None:
        return False

    lowered = text.lower()
    if any(marker in text for marker in _COMPLETION_TEXT_MARKERS[:3]):
        return False
    if any(marker in lowered for marker in _COMPLETION_TEXT_MARKERS[3:]):
        return False

    has_question_signal = any(marker in text for marker in _IMPLICIT_CLARIFICATION_MARKERS[:5])
    has_question_signal = has_question_signal or any(marker in lowered for marker in _IMPLICIT_CLARIFICATION_MARKERS[5:])
    has_question_signal = has_question_signal or "?" in text or "？" in text

    return has_question_signal or _contains_choice_enumeration(text)
