"""ReAct engine builder — short tool-driven action loops."""

from __future__ import annotations

from src.agents.lead_agent.engines.base import BaseEngineBuilder, EnginePromptKwargs


class ReactEngineBuilder(BaseEngineBuilder):
    """ReAct engine: explicit react prompt mode, no tool filtering."""

    @property
    def canonical_name(self) -> str:
        return "react"

    @property
    def aliases(self) -> list[str]:
        return ["ReAct"]

    def build_prompt_kwargs(self) -> EnginePromptKwargs:
        return EnginePromptKwargs(engine_mode="react")
