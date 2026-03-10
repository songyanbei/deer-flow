"""Laifu-compatible subagent delegation tool."""

from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadState

from .subagent_delegate import run_subagent_task


@tool("yield_for_help", parse_docstring=True)
def yield_for_help_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    missing_information_desc: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    hint_agent: str = "",
) -> str:
    """Delegate missing-information lookup to another subagent.

    Args:
        missing_information_desc: Concrete information that another specialist agent should fetch.
        hint_agent: Optional target builtin subagent ID such as `agent_contacts_01`.
    """
    subagent_type = hint_agent or "general-purpose"
    delegated_prompt = (
        "请作为支援子智能体完成以下信息查询，并只返回查询结果本身：\n"
        f"{missing_information_desc}"
    )
    result = run_subagent_task(
        runtime=runtime,
        description="查询缺失信息",
        prompt=delegated_prompt,
        subagent_type=subagent_type,
        tool_call_id=tool_call_id,
        emit_events=False,
    )
    success_prefix = "Task Succeeded. Result: "
    if result.startswith(success_prefix):
        return result[len(success_prefix):]
    return result
