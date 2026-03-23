"""Default engine builder — preserves legacy/default agent behavior."""

from __future__ import annotations

from src.agents.lead_agent.engines.base import BaseEngineBuilder


class DefaultEngineBuilder(BaseEngineBuilder):
    """Default engine: no special prompt mode, no tool filtering."""

    @property
    def canonical_name(self) -> str:
        return "default"
