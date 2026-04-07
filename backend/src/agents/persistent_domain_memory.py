from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.memory.prompt import format_memory_for_injection
from src.agents.memory.queue import get_memory_queue
from src.agents.memory.updater import get_memory_data
from src.agents.thread_state import TaskStatus
from src.config.agents_config import load_agent_config, load_agent_runbook

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform-level hint extractor contract
# ---------------------------------------------------------------------------

class DomainHintExtractor(ABC):
    """Abstract base for domain-specific hint extractors.

    Each domain that activates the ``persistent_domain_memory`` capability
    profile should register a concrete extractor.  The platform entry point
    (``_extract_persistent_memory_hints``) dispatches to the registered
    extractor by *domain* (not agent name), so extractor logic is decoupled
    from a specific agent identity.

    Subclasses must be registered via :func:`register_hint_extractor`.
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """The business domain this extractor serves (e.g. ``"meeting"``)."""

    @abstractmethod
    def extract(
        self,
        task: TaskStatus,
        verified_fact: Mapping[str, Any],
    ) -> list[tuple[str, str]]:
        """Return ``(label, value)`` hint pairs from a verified task.

        Implementations should:
        * only extract *stable, reusable* preference hints;
        * never include transactional outputs (IDs, attendee lists, etc.);
        * deduplicate before returning.
        """


# ---------------------------------------------------------------------------
# Hint extractor registry (platform level)
# ---------------------------------------------------------------------------

_hint_extractors: dict[str, DomainHintExtractor] = {}


def register_hint_extractor(extractor: DomainHintExtractor) -> None:
    """Register a domain hint extractor.  Overwrites any previous extractor
    for the same domain."""
    _hint_extractors[extractor.domain] = extractor
    logger.info(
        "[PersistentDomainMemory] Registered hint extractor for domain '%s'",
        extractor.domain,
    )


def get_hint_extractor(domain: str | None) -> DomainHintExtractor | None:
    """Return the registered extractor for *domain*, or ``None``."""
    if domain is None:
        return None
    return _hint_extractors.get(domain)


def list_registered_extractors() -> dict[str, str]:
    """Return ``{domain: class_name}`` for introspection."""
    return {d: type(e).__name__ for d, e in _hint_extractors.items()}


# ---------------------------------------------------------------------------
# Common hint-extraction utilities (shared by all domain extractors)
# ---------------------------------------------------------------------------

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


def collect_allowed_hints(
    value: Any,
    *,
    allow_fields: Mapping[str, str],
    collected: list[tuple[str, str]],
) -> None:
    """Recursively collect ``(label, value)`` hint pairs from *value*
    wherever keys match *allow_fields*.

    This is a platform utility shared by all domain hint extractors.
    """
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            normalized_key = _normalize_key(raw_key)
            label = allow_fields.get(normalized_key)
            if label:
                rendered = _render_hint_value(child)
                if rendered:
                    collected.append((label, rendered))
            collect_allowed_hints(child, allow_fields=allow_fields, collected=collected)
        return

    if isinstance(value, list):
        for item in value:
            collect_allowed_hints(item, allow_fields=allow_fields, collected=collected)


def dedupe_hint_items(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Deduplicate ``(label, value)`` pairs preserving order."""
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


# ---------------------------------------------------------------------------
# Pilot: meeting-agent hint extractor (domain-specific, not yet generalised)
# ---------------------------------------------------------------------------

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


class MeetingHintExtractor(DomainHintExtractor):
    """Pilot hint extractor for the ``meeting`` domain.

    .. admonition:: Pilot / Experimental

       This extractor contains domain-specific allowed-field lists that have
       only been validated for ``meeting-agent``.  It is registered
       automatically at module load time but should **not** be copied
       directly for new domains — new domains must define their own
       extractor with their own allowlist and register it via
       :func:`register_hint_extractor`.
    """

    @property
    def domain(self) -> str:
        return "meeting"

    def extract(
        self,
        task: TaskStatus,
        verified_fact: Mapping[str, Any],
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []

        resolved_inputs = task.get("resolved_inputs")
        if isinstance(resolved_inputs, Mapping):
            collect_allowed_hints(resolved_inputs, allow_fields=_MEETING_ALLOWED_HINT_FIELDS, collected=candidates)

        payload = verified_fact.get("payload")
        if isinstance(payload, Mapping):
            collect_allowed_hints(payload, allow_fields=_MEETING_ALLOWED_HINT_FIELDS, collected=candidates)

        return dedupe_hint_items(candidates)


# Auto-register the meeting pilot extractor.
register_hint_extractor(MeetingHintExtractor())


# ---------------------------------------------------------------------------
# Platform dispatch — resolves domain from agent config
# ---------------------------------------------------------------------------

def _resolve_agent_domain(agent_name: str | None, *, agents_dir=None) -> str | None:
    """Return the ``domain`` for an agent, or ``None``."""
    if not agent_name:
        return None
    try:
        cfg = load_agent_config(agent_name, agents_dir=agents_dir)
        return cfg.domain if cfg else None
    except Exception:
        return None


def _extract_persistent_memory_hints(
    agent_name: str | None,
    task: TaskStatus,
    verified_fact: Mapping[str, Any],
    *,
    agents_dir=None,
) -> list[tuple[str, str]]:
    """Platform entry point: dispatch to the registered domain extractor."""
    domain = _resolve_agent_domain(agent_name, agents_dir=agents_dir)
    extractor = get_hint_extractor(domain)
    if extractor is None:
        return []
    return extractor.extract(task, verified_fact)


def is_persistent_domain_memory_enabled(agent_name: str | None, *, agents_dir=None) -> bool:
    if not agent_name:
        return False
    try:
        agent_cfg = load_agent_config(agent_name, agents_dir=agents_dir)
    except Exception:
        return False
    return bool(agent_cfg and agent_cfg.persistent_memory_enabled)


def get_persistent_domain_memory_context(agent_name: str | None, *, max_tokens: int = 1200, tenant_id: str | None = None, user_id: str | None = None, agents_dir=None) -> str:
    if not is_persistent_domain_memory_enabled(agent_name, agents_dir=agents_dir):
        return ""

    try:
        memory_data = get_memory_data(agent_name, tenant_id=tenant_id, user_id=user_id)
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


def get_persistent_domain_runbook(agent_name: str | None, *, agents_dir=None) -> str:
    """Return the runbook content for an agent.

    Delegates entirely to :func:`load_agent_runbook`, which loads the
    runbook when **any** of the following is true:

    * ``persistent_memory_enabled`` is set (original behaviour), OR
    * ``persistent_runbook_file`` is explicitly configured, OR
    * a default ``RUNBOOK.md`` exists on disk for the agent.

    This allows ``domain_runbook_support`` to work independently of
    the ``persistent_domain_memory`` profile.
    """
    return load_agent_runbook(agent_name, agents_dir=agents_dir) or ""


def _build_verified_task_memory_messages(
    agent_name: str | None,
    task: TaskStatus,
    verified_fact: Mapping[str, Any],
    *,
    agents_dir=None,
) -> list[Any]:
    hint_items = _extract_persistent_memory_hints(agent_name, task, verified_fact, agents_dir=agents_dir)
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
    tenant_id: str | None = None,
    agents_dir=None,
) -> bool:
    if not is_persistent_domain_memory_enabled(agent_name, agents_dir=agents_dir):
        return False
    if not isinstance(verified_fact, Mapping):
        return False

    messages = _build_verified_task_memory_messages(agent_name, task, verified_fact, agents_dir=agents_dir)
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
            tenant_id=tenant_id,
            dedupe_key=f"persistent-domain:{tenant_id or 'default'}:{agent_name}:{effective_thread_id}:{task_key}",
        )
        return True
    except Exception as exc:
        logger.warning(
            "[PersistentDomainMemory] Failed to queue memory update for agent '%s': %s",
            agent_name,
            exc,
        )
        return False
