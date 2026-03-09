"""Middleware to enforce per-agent tool call limits."""

from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime


class ToolCallLimitMiddleware(AgentMiddleware[AgentState]):
    """Stop an agent once it exceeds its configured tool usage budget."""

    def __init__(self, max_tool_calls: int):
        super().__init__()
        self.max_tool_calls = max(1, max_tool_calls)

    def _enforce_limit(self, state: AgentState) -> None:
        tool_message_count = sum(1 for msg in state.get("messages", []) if getattr(msg, "type", None) == "tool")
        if tool_message_count >= self.max_tool_calls:
            raise RuntimeError(f"Tool call limit exceeded: {tool_message_count}/{self.max_tool_calls}")

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._enforce_limit(state)
        return None

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._enforce_limit(state)
        return None
