from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.memory.prompt import format_memory_for_injection
from src.agents.memory.queue import get_memory_queue
from src.agents.memory.updater import get_memory_data
from src.agents.thread_state import TaskStatus
from src.config.agents_config import load_agent_config, load_agent_runbook

logger = logging.getLogger(__name__)

_MEETING_ALLOWED_HINT_FIELDS: dict[str, str] = {
    "city": "Preferred booking city",
    "city_name": "Preferred booking city",
    "location": "Preferred booking city",
    "preferred_city": "Preferred booking city",
    "fallback_city": "City fallback hint",
    "fallback_cities": "City fallback hint",
    "department": "Organizer department hint",
    "dept": "Organizer department hint",
    "organizer": "Recurring organizer hint",
    "organizer_name": "Recurring organizer hint",
    "room_preference": "Preferred room characteristics",
    "room_preferences": "Preferred room characteristics",
    "room_feature": "Preferred room characteristics",
    "room_features": "Preferred room characteristics",
    "preferred_features": "Preferred room characteristics",
    "capacity": "Preferred capacity hint",
    "booking_window": "Preferred booking window",
    "meeting_window": "Preferred booking window",
    "time_preference": "Preferred booking window",
    "time_preferences": "Preferred booking window",
}
_NULLISH_VALUES = {"", "none", "null", "undefined"}
def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _render_hint_value(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        return "" if text.lower() in _NULLISH_VALUES else text
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        rendered_items = [_render_hint_value(item) for item in value]
        rendered_items = [item for item in rendered_items if item]
        return ", ".join(rendered_items)
    return ""


def _collect_allowed_hints(
    value: Any,
    *,
    allow_fields: Mapping[str, str],
    collected: list[tuple[str, str]],
) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            normalized_key = _normalize_key(raw_key)
            label = allow_fields.get(normalized_key)
            if label:
                rendered = _render_hint_value(child)
                if rendered:
                    collected.append((label, rendered))
            _collect_allowed_hints(child, allow_fields=allow_fields, collected=collected)
        return

    if isinstance(value, list):
        for item in value:
            _collect_allowed_hints(item, allow_fields=allow_fields, collected=collected)


def _dedupe_hint_items(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _extract_meeting_memory_hints(
    task: TaskStatus,
    verified_fact: Mapping[str, Any],
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    resolved_inputs = task.get("resolved_inputs")
    if isinstance(resolved_inputs, Mapping):
        _collect_allowed_hints(resolved_inputs, allow_fields=_MEETING_ALLOWED_HINT_FIELDS, collected=candidates)

    payload = verified_fact.get("payload")
    if isinstance(payload, Mapping):
        _collect_allowed_hints(payload, allow_fields=_MEETING_ALLOWED_HINT_FIELDS, collected=candidates)

    return _dedupe_hint_items(candidates)


def _extract_persistent_memory_hints(
    agent_name: str | None,
    task: TaskStatus,
    verified_fact: Mapping[str, Any],
) -> list[tuple[str, str]]:
    if agent_name == "meeting-agent":
        return _extract_meeting_memory_hints(task, verified_fact)
    return []


def is_persistent_domain_memory_enabled(agent_name: str | None) -> bool:
    if not agent_name:
        return False
    try:
        agent_cfg = load_agent_config(agent_name)
    except Exception:
        return False
    return bool(agent_cfg and agent_cfg.persistent_memory_enabled)


def get_persistent_domain_memory_context(agent_name: str | None, *, max_tokens: int = 1200) -> str:
    if not is_persistent_domain_memory_enabled(agent_name):
        return ""

    try:
        memory_data = get_memory_data(agent_name)
    except Exception as exc:
        logger.warning(
            "[PersistentDomainMemory] Failed to load memory context for agent '%s': %s",
            agent_name,
            exc,
        )
        return ""
    if not isinstance(memory_data, dict) or not memory_data:
        return ""

    try:
        formatted = format_memory_for_injection(memory_data, max_tokens=max_tokens).strip()
    except Exception as exc:
        logger.warning(
            "[PersistentDomainMemory] Failed to format memory context for agent '%s': %s",
            agent_name,
            exc,
        )
        return ""
    return formatted


def get_persistent_domain_runbook(agent_name: str | None) -> str:
    if not is_persistent_domain_memory_enabled(agent_name):
        return ""
    return load_agent_runbook(agent_name) or ""


def _build_verified_task_memory_messages(
    agent_name: str | None,
    task: TaskStatus,
    verified_fact: Mapping[str, Any],
) -> list[Any]:
    hint_items = _extract_persistent_memory_hints(agent_name, task, verified_fact)
    if not hint_items:
        return []

    seed_lines = [
        "Persistent domain hints from a verified successful workflow.",
        "Keep only stable reusable preferences or routing hints. Do not retain transactional booking outputs.",
        f"Task: {task.get('description') or ''}".strip(),
        "Reusable hints:\n" + "\n".join(f"- {label}: {value}" for label, value in hint_items),
    ]
    summary = "; ".join(f"{label}: {value}" for label, value in hint_items)

    return [
        HumanMessage(content="\n\n".join(line for line in seed_lines if line)),
        AIMessage(content=summary),
    ]


def queue_persistent_domain_memory_update(
    agent_name: str | None,
    *,
    task: TaskStatus,
    verified_fact: Mapping[str, Any] | None,
    thread_id: str | None,
) -> bool:
    if not is_persistent_domain_memory_enabled(agent_name):
        return False
    if not isinstance(verified_fact, Mapping):
        return False

    messages = _build_verified_task_memory_messages(agent_name, task, verified_fact)
    if not messages:
        return False

    effective_thread_id = str(thread_id or task.get("run_id") or task.get("task_id") or "").strip()
    task_key = str(task.get("task_id") or task.get("run_id") or "").strip()
    if not effective_thread_id or not task_key:
        return False

    try:
        get_memory_queue().add(
            thread_id=effective_thread_id,
            messages=messages,
            agent_name=agent_name,
            dedupe_key=f"persistent-domain:{agent_name}:{effective_thread_id}:{task_key}",
        )
        return True
    except Exception as exc:
        logger.warning(
            "[PersistentDomainMemory] Failed to queue memory update for agent '%s': %s",
            agent_name,
            exc,
        )
        return False
