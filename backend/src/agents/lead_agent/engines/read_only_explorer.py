"""Read-Only Explorer engine builder — filters write-like MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agents.lead_agent.engines.base import BaseEngineBuilder, EnginePromptKwargs, EngineRuntimeOptions

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


class ReadOnlyExplorerEngineBuilder(BaseEngineBuilder):
    """Read-Only Explorer engine: read-only prompt mode with write-tool filtering."""

    @property
    def canonical_name(self) -> str:
        return "read_only_explorer"

    @property
    def aliases(self) -> list[str]:
        return ["ReadOnly_Explorer", "readonly", "readonly_explorer"]

    def build_prompt_kwargs(self) -> EnginePromptKwargs:
        return EnginePromptKwargs(engine_mode="read_only_explorer")

    def prepare_extra_tools(self, tools: list[BaseTool]) -> list[BaseTool]:
        from src.mcp.tool_filter import filter_read_only_tools

        return filter_read_only_tools(tools)

    def prepare_runtime_options(self) -> EngineRuntimeOptions:
        return EngineRuntimeOptions(filter_read_only_tools=True)
