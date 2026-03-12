"""Middleware for intercepting request_help tool calls from workflow domain agents."""

import json
from collections.abc import Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command


class HelpRequestMiddlewareState(AgentState):
    """Compatible with ThreadState."""

    pass


class HelpRequestMiddleware(AgentMiddleware[HelpRequestMiddlewareState]):
    """Interrupt workflow domain-agent execution when a request_help tool call is emitted."""

    state_schema = HelpRequestMiddlewareState

    def _serialize_payload(self, args: dict[str, Any]) -> str:
        payload = {
            "problem": str(args.get("problem", "")).strip(),
            "required_capability": str(args.get("required_capability", "")).strip(),
            "reason": str(args.get("reason", "")).strip(),
            "expected_output": str(args.get("expected_output", "")).strip(),
            "resolution_strategy": str(args.get("resolution_strategy", "")).strip() or None,
            "clarification_question": str(args.get("clarification_question", "")).strip() or None,
            "clarification_options": args.get("clarification_options"),
            "clarification_context": str(args.get("clarification_context", "")).strip() or None,
            "context_payload": args.get("context_payload"),
            "candidate_agents": args.get("candidate_agents"),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _handle_help_request(self, request: ToolCallRequest) -> Command:
        tool_call_id = request.tool_call.get("id", "")
        args = request.tool_call.get("args", {})
        tool_message = ToolMessage(
            content=self._serialize_payload(args),
            tool_call_id=tool_call_id,
            name="request_help",
        )
        return Command(update={"messages": [tool_message]}, goto=END)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "request_help":
            return handler(request)
        return self._handle_help_request(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "request_help":
            return await handler(request)
        return self._handle_help_request(request)
