"""Shared helpers for delegating work to builtin subagents."""

import logging
import time
import uuid
from dataclasses import replace

from langgraph.config import get_stream_writer

from src.agents.lead_agent.prompt import get_skills_prompt_section
from src.subagents import SubagentExecutor, get_subagent_config
from src.subagents.executor import SubagentStatus, get_background_task_result

logger = logging.getLogger(__name__)


def run_subagent_task(
    *,
    runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: str,
    max_turns: int | None = None,
    emit_events: bool = True,
) -> str:
    """Execute a subagent task and wait for the final result."""
    config = get_subagent_config(subagent_type)
    if config is None:
        from src.subagents.registry import get_subagent_names

        available = ", ".join(get_subagent_names())
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

    overrides: dict = {}
    skills_section = get_skills_prompt_section()
    if skills_section:
        overrides["system_prompt"] = config.system_prompt + "\n\n" + skills_section
    if max_turns is not None:
        overrides["max_turns"] = max_turns
    if overrides:
        config = replace(config, **overrides)

    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None
    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id")
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    from src.tools import get_available_tools
    from src.tools.tools import get_fresh_private_subagent_tools

    tools = get_available_tools(
        model_name=parent_model,
        subagent_enabled=False,
        include_private_tools=False,
    )
    tools.extend(get_fresh_private_subagent_tools())

    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=parent_model,
        sandbox_state=sandbox_state,
        thread_data=thread_data,
        thread_id=thread_id,
        trace_id=trace_id,
    )
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    poll_count = 0
    last_status = None
    last_message_count = 0
    max_poll_count = (config.timeout_seconds + 60) // 5
    writer = get_stream_writer()

    if emit_events and writer is not None:
        writer({"type": "task_started", "task_id": task_id, "description": description})

    while True:
        result = get_background_task_result(task_id)
        if result is None:
            if emit_events and writer is not None:
                writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
            return f"Error: Task {task_id} disappeared from background tasks"

        if result.status != last_status:
            logger.info("[trace=%s] Task %s status: %s", trace_id, task_id, result.status.value)
            last_status = result.status

        current_message_count = len(result.ai_messages)
        if emit_events and writer is not None and current_message_count > last_message_count:
            for i in range(last_message_count, current_message_count):
                writer(
                    {
                        "type": "task_running",
                        "task_id": task_id,
                        "message": result.ai_messages[i],
                        "message_index": i + 1,
                        "total_messages": current_message_count,
                    }
                )
            last_message_count = current_message_count

        if result.status == SubagentStatus.COMPLETED:
            if emit_events and writer is not None:
                writer({"type": "task_completed", "task_id": task_id, "result": result.result})
            return f"Task Succeeded. Result: {result.result}"
        if result.status == SubagentStatus.FAILED:
            if emit_events and writer is not None:
                writer({"type": "task_failed", "task_id": task_id, "error": result.error})
            return f"Task failed. Error: {result.error}"
        if result.status == SubagentStatus.TIMED_OUT:
            if emit_events and writer is not None:
                writer({"type": "task_timed_out", "task_id": task_id, "error": result.error})
            return f"Task timed out. Error: {result.error}"

        time.sleep(5)
        poll_count += 1
        if poll_count > max_poll_count:
            timeout_minutes = config.timeout_seconds // 60
            if emit_events and writer is not None:
                writer({"type": "task_timed_out", "task_id": task_id})
            return (
                f"Task polling timed out after {timeout_minutes} minutes. "
                f"This may indicate the background task is stuck. Status: {result.status.value}"
            )
