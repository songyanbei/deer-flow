from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.config.agents_config import AgentConfig

logger = logging.getLogger(__name__)

EngineMode = Literal["default", "react", "read_only_explorer", "sop"]

_ENGINE_TYPE_ALIASES: dict[str, EngineMode] = {
    "react": "react",
    "readonly": "read_only_explorer",
    "readonly_explorer": "read_only_explorer",
    "sop": "sop",
    "sop_engine": "sop",
}


@dataclass(frozen=True)
class EngineBehavior:
    mode: EngineMode
    explicit_engine_type: str | None
    filter_read_only_tools: bool = False


def resolve_engine_behavior(agent_config: AgentConfig | None) -> EngineBehavior:
    """Resolve the runtime engine behavior for a configured agent.

    When `engine_type` is omitted, keep the current legacy/default builder path.
    Supported explicit engine types are limited to ReadOnly_Explorer, ReAct, and SOP.
    Unknown values fall back to the default mode for safety.
    """

    if agent_config is None:
        return EngineBehavior(mode="default", explicit_engine_type=None)

    raw_engine_type = (agent_config.engine_type or "").strip()
    if not raw_engine_type:
        return EngineBehavior(mode="default", explicit_engine_type=None)

    normalized = _ENGINE_TYPE_ALIASES.get(raw_engine_type.lower())
    if normalized is None:
        logger.warning(
            "Agent '%s' configured unsupported engine_type '%s'; falling back to default mode.",
            agent_config.name,
            raw_engine_type,
        )
        return EngineBehavior(mode="default", explicit_engine_type=raw_engine_type)

    return EngineBehavior(
        mode=normalized,
        explicit_engine_type=raw_engine_type,
        filter_read_only_tools=(normalized == "read_only_explorer"),
    )
