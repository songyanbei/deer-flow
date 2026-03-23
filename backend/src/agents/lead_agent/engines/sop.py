"""SOP engine builder — step-by-step domain procedure execution."""

from __future__ import annotations

from src.agents.lead_agent.engines.base import BaseEngineBuilder, EnginePromptKwargs


class SopEngineBuilder(BaseEngineBuilder):
    """SOP engine: procedure-driven prompt mode, no tool filtering."""

    @property
    def canonical_name(self) -> str:
        return "sop"

    @property
    def aliases(self) -> list[str]:
        return ["SOP", "sop_engine"]

    def build_prompt_kwargs(self) -> EnginePromptKwargs:
        return EnginePromptKwargs(engine_mode="sop")
