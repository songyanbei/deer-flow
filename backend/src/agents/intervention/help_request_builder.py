"""Shared help-request-to-intervention builder.

Extracted from ``semantic_router.py`` so that both the executor (direct
user-owned intervention) and the router (compatibility / fallback path)
can build ``InterventionRequest`` payloads without duplicating protocol
assembly logic.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from src.agents.intervention.fingerprint import generate_clarification_semantic_fingerprint
from src.agents.thread_state import (
    HelpRequestPayload,
    InterventionRequest,
    TaskStatus,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Clarification option helpers
# ---------------------------------------------------------------------------

def normalize_clarification_options(options: Any) -> list[str]:
    """Normalize raw clarification options list into cleaned strings."""
    if not isinstance(options, list):
        return []
    return [str(option).strip() for option in options if str(option).strip()]


def build_intervention_options(options: list[str]) -> list[dict[str, str]]:
    """Build option entries for an ``InterventionActionSchema``."""
    return [{"label": option, "value": option} for option in options]


# ---------------------------------------------------------------------------
# Interaction kind resolution
# ---------------------------------------------------------------------------

def resolve_user_interaction_kind(help_request: HelpRequestPayload, options: list[str]) -> str:
    """Determine the UI interaction kind from a help request payload."""
    strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
    if strategy == "user_confirmation":
        return "confirm"
    if strategy == "user_multi_select":
        return "multi_select"
    if options:
        return "single_select"
    return "input"


# ---------------------------------------------------------------------------
# User-clarification detection
# ---------------------------------------------------------------------------

def should_interrupt_for_user_clarification(help_request: HelpRequestPayload) -> bool:
    """Return *True* when *help_request* represents a user-owned blocking step."""
    strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
    return strategy in {"user_clarification", "user_confirmation", "user_multi_select"} or bool(
        str(help_request.get("clarification_question") or "").strip()
    )


# ---------------------------------------------------------------------------
# Question extraction & rendering helpers
# ---------------------------------------------------------------------------

def _clean_question_segment(segment: str) -> str:
    fullwidth_comma = chr(0xFF0C)
    fullwidth_exclamation = chr(0xFF01)
    fullwidth_colon = chr(0xFF1A)
    text = str(segment or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"^\s*(?:\d+[\.、\)]\s*|[-—–•·]+\s*)",
        "",
        text,
    ).strip()
    text = re.sub(
        rf"^(?:你好[!{fullwidth_exclamation},{fullwidth_comma}\s]*|您好[!{fullwidth_exclamation},{fullwidth_comma}\s]*|麻烦您[,{fullwidth_comma}\s]*)",
        "",
        text,
    ).strip()
    if fullwidth_colon in text:
        prefix, suffix = text.split(fullwidth_colon, 1)
        if suffix.strip() and any(
            token in prefix
            for token in (
                "请",
                "提供",
                "告诉",
                "信息",
            )
        ):
            text = suffix.strip()
    return text.strip()


def _extract_clarification_questions(question: str) -> list[str]:
    fullwidth_qmark = chr(0xFF1F)
    normalized = str(question or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []

    raw_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    numbered_lines = [
        line for line in raw_lines if re.match(r"^\d+[\.、\)]\s*", line)
    ]
    if len(numbered_lines) >= 2:
        return [
            cleaned
            for cleaned in (_clean_question_segment(line) for line in numbered_lines)
            if cleaned
        ]

    line_questions = [
        _clean_question_segment(line)
        for line in raw_lines
        if fullwidth_qmark in line or "?" in line
    ]
    line_questions = [line for line in line_questions if line]
    if len(line_questions) >= 2:
        return line_questions

    sentence_candidates: list[str] = []
    buffer = ""
    for char in normalized:
        buffer += char
        if char in {fullwidth_qmark, "?"}:
            cleaned = _clean_question_segment(buffer)
            if cleaned:
                sentence_candidates.append(cleaned)
            buffer = ""
    if buffer.strip():
        cleaned = _clean_question_segment(buffer)
        if cleaned:
            sentence_candidates.append(cleaned)
    question_candidates = [
        part
        for part in sentence_candidates
        if part and (fullwidth_qmark in part or "?" in part)
    ]
    if len(question_candidates) >= 2:
        return question_candidates

    cleaned = _clean_question_segment(normalized)
    return [cleaned] if cleaned else []


def _is_renderable_intervention_question(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    normalized = text.lower()
    if re.fullmatch(r"[-—–•_\s]+", text):
        return False
    explanatory_patterns = (
        "请提供以下",
        "请补充以下",
        "我需要更多信息",
        "我需要以下",
        "需要以下",
        "为了帮您",
        "为了帮助您",
        "为了给您",
        "为了继续",
        "包括",
        "如下",
        "基础信息",
        "关键信息",
        "please provide the following",
        "please answer the following",
        "i need more information",
        "i need the following",
        "to continue",
        "to help",
        "the following details",
        "key information",
        "basic information",
    )
    if any(pattern in text for pattern in explanatory_patterns):
        return False
    if text.endswith(":") or text.endswith("："):
        return False
    if any(token in text for token in ("？", "?")):
        return True
    return bool(
        re.match(
            r"^(请问|请填写|请提供|请补充|请输入|请选择|是否|能否|可否|what\b|when\b|where\b|who\b|which\b|how\b)",
            normalized,
        )
    )


def _infer_question_kind(question: str, *, index: int, options: list[str]) -> str:
    lowered = question.lower()
    if index == 0 and options:
        return "single_select"
    if any(token in question for token in ("是否", "确认", "同意")) or lowered.startswith("is "):
        return "confirm"
    return "input"


def _build_intervention_questions(question: str, options: list[str]) -> list[dict[str, Any]]:
    question_parts = [
        part
        for part in _extract_clarification_questions(question)
        if _is_renderable_intervention_question(part)
    ]
    if len(question_parts) < 2:
        return []

    questions: list[dict[str, Any]] = []
    for index, part in enumerate(question_parts):
        kind = _infer_question_kind(part, index=index, options=options)
        entry: dict[str, Any] = {
            "key": f"question_{index + 1}",
            "label": part,
            "kind": kind,
            "required": True,
        }
        if kind == "single_select":
            entry["options"] = build_intervention_options(options)
            entry["min_select"] = 1
            entry["max_select"] = 1
        elif kind == "input":
            entry["placeholder"] = part
        elif kind == "confirm":
            entry["confirm_text"] = "确认"
        questions.append(entry)
    return questions


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_help_request_intervention(
    parent_task: TaskStatus,
    help_request: HelpRequestPayload,
    *,
    agent_name: str,
) -> InterventionRequest:
    """Build a complete ``InterventionRequest`` from a help-request payload.

    This is the single authoritative builder used by both the executor
    (for direct user-owned intervention) and the router (for compatibility
    fallback).
    """
    question = str(help_request.get("clarification_question") or "").strip()
    context = str(help_request.get("clarification_context") or "").strip()
    options = normalize_clarification_options(help_request.get("clarification_options"))
    interaction_kind = resolve_user_interaction_kind(help_request, options)
    questions = _build_intervention_questions(question, options)
    request_id = f"intv_{uuid.uuid4().hex[:12]}"
    fingerprint = generate_clarification_semantic_fingerprint(agent_name, question, options)
    title = question or "需要您的确认"
    reason = context or help_request.get("reason", "").strip() or title

    action: dict[str, Any] = {
        "key": "submit_response",
        "label": "确认" if interaction_kind == "confirm" else "提交回复",
        "kind": "composite" if questions else interaction_kind,
        "resolution_behavior": "resume_current_task",
        "required": True,
    }

    if questions:
        action["confirm_text"] = "提交回复"
    elif interaction_kind == "input":
        action["placeholder"] = question or "请输入您的回复"
        action["confirm_text"] = "提交回复"
    elif interaction_kind == "confirm":
        action["confirm_text"] = "确认"
    elif interaction_kind == "single_select":
        action["options"] = build_intervention_options(options)
        action["min_select"] = 1
        action["max_select"] = 1
        action["confirm_text"] = "确认选择"
    elif interaction_kind == "multi_select":
        action["options"] = build_intervention_options(options)
        action["min_select"] = 1
        action["max_select"] = len(options)
        action["confirm_text"] = "确认选择"

    return {
        "request_id": request_id,
        "fingerprint": fingerprint,
        "interrupt_kind": "clarification" if interaction_kind == "input" else ("confirmation" if interaction_kind == "confirm" else "selection"),
        "semantic_key": fingerprint,
        "source_signal": "request_help",
        "intervention_type": "clarification",
        "title": title,
        "reason": reason,
        "description": context or None,
        "source_agent": agent_name,
        "source_task_id": parent_task["task_id"],
        "category": "user_clarification",
        "action_summary": title,
        "context": help_request.get("context_payload"),
        "action_schema": {
            "actions": [action],
        },
        "questions": questions or None,
        "created_at": _utc_now_iso(),
    }
