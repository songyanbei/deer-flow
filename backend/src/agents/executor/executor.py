"""Domain Agent executor node for the multi-agent graph."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.thread_state import TaskStatus, ThreadState, VerifiedFact
from src.config.agents_config import load_agent_config

logger = logging.getLogger(__name__)

_mcp_initialized: set[str] = set()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _extract_latest_clarification_answer(state: ThreadState) -> str:
    messages = state.get("messages") or []
    if len(messages) < 2:
        return ""

    last = messages[-1]
    prev = messages[-2]
    is_last_human = getattr(last, "type", None) == "human" or last.__class__.__name__ == "HumanMessage"
    if not is_last_human:
        return ""

    prev_name = getattr(prev, "name", None)
    prev_content = _content_to_text(getattr(prev, "content", ""))
    if prev_name == "ask_clarification" or "clarif" in prev_content.lower() or "clarification" in prev_content.lower():
        return _content_to_text(getattr(last, "content", ""))
    return ""


def _fact_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "summary" in value:
            return str(value["summary"])
        if "result" in value:
            return str(value["result"])
    return str(value)


def _build_context(task: TaskStatus, verified_facts: VerifiedFact, clarification_answer: str = "") -> str:
    context = task["description"]

    if verified_facts:
        facts_block = "\n".join(
            f"  {i + 1}. [{fact_key}] {_fact_value_to_text(fact_value)}"
            for i, (fact_key, fact_value) in enumerate(verified_facts.items())
        )
        context += f"\n\nKnown facts (do not re-check):\n{facts_block}"

    if clarification_answer:
        context += f"\n\nUser clarification answer:\n{clarification_answer}"

    return context


async def _ensure_mcp_ready(agent_name: str) -> None:
    if agent_name in _mcp_initialized:
        return
    agent_cfg = load_agent_config(agent_name)
    if agent_cfg and agent_cfg.mcp_servers:
        try:
            from src.execution.mcp_pool import mcp_pool

            servers = [s.model_dump() for s in agent_cfg.mcp_servers]
            success = await mcp_pool.init_agent_connections(agent_name, servers)
            if not success:
                error = mcp_pool.get_agent_error(agent_name) or "unknown MCP connection error"
                raise RuntimeError(error)
            logger.info("[Executor] MCP ready for agent '%s' (%d server(s)).", agent_name, len(servers))
        except Exception as e:
            logger.error("[Executor] MCP init failed for agent '%s': %s", agent_name, e)
            raise
    _mcp_initialized.add(agent_name)


def _get_latest_fact_text(state: ThreadState) -> str:
    facts = state.get("verified_facts") or {}
    if not facts:
        return "Task completed."
    last_key = next(reversed(facts))
    return _fact_value_to_text(facts[last_key])


def _get_event_writer() -> Callable[[dict[str, Any]], None]:
    try:
        return get_stream_writer()
    except Exception:
        return lambda _event: None


def _default_event_status(event_type: str) -> str:
    if event_type in {"task_started", "task_running"}:
        return "in_progress"
    if event_type == "task_completed":
        return "completed"
    if event_type == "task_failed":
        return "failed"
    return "unknown"


def _resolve_task_run_id(state: ThreadState, task: TaskStatus) -> str:
    return task.get("run_id") or state.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"


def _emit_task_event(writer: Callable[[dict[str, Any]], None], event_type: str, task: TaskStatus, agent_name: str, **extra: Any) -> None:
    payload = {
        "type": event_type,
        "source": "multi_agent",
        "run_id": task.get("run_id"),
        "task_id": task["task_id"],
        "agent_name": agent_name,
        "description": task["description"],
        "status": _default_event_status(event_type),
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    writer(
        payload
    )


def _handle_system_special(agent_name: str, task: TaskStatus, state: ThreadState, writer: Callable[[dict[str, Any]], None]) -> dict:
    if agent_name == "SYSTEM_FINISH":
        final = _get_latest_fact_text(state)
        done_task: TaskStatus = {
            **task,
            "status": "DONE",
            "result": final,
            "status_detail": "Task completed by system shortcut",
            "clarification_prompt": None,
            "updated_at": _utc_now_iso(),
        }  # type: ignore[typeddict-item]
        _emit_task_event(writer, "task_completed", task, agent_name, result=final)
        return {"task_pool": [done_task], "execution_state": "EXECUTING_DONE", "final_result": final}

    fallback_msg = "System fallback: no domain agent could handle this task."
    failed_task: TaskStatus = {
        **task,
        "status": "FAILED",
        "error": fallback_msg,
        "status_detail": fallback_msg,
        "clarification_prompt": None,
        "updated_at": _utc_now_iso(),
    }  # type: ignore[typeddict-item]
    _emit_task_event(writer, "task_failed", task, agent_name, error=fallback_msg)
    return {"task_pool": [failed_task], "execution_state": "EXECUTING_DONE"}


def _extract_agent_output(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "ask_clarification":
            continue
        text = _content_to_text(getattr(message, "content", ""))
        if text.strip():
            return text.strip()
    return ""


async def executor_node(state: ThreadState, config: RunnableConfig) -> dict:
    task_pool: list[TaskStatus] = state.get("task_pool") or []
    running = [t for t in task_pool if t["status"] == "RUNNING"]

    if not running:
        logger.error("[Executor] Called with no RUNNING task.")
        return {"execution_state": "ERROR", "final_result": "[Executor] No RUNNING task found."}

    task = running[0]
    task_run_id = _resolve_task_run_id(state, task)
    task = {
        **task,
        "run_id": task_run_id,
        "updated_at": task.get("updated_at") or _utc_now_iso(),
    }  # type: ignore[typeddict-item]
    agent_name = task.get("assigned_agent") or "SYSTEM_FALLBACK"
    writer = _get_event_writer()
    logger.info("[Executor] Executing task '%s' via agent '%s'.", task["task_id"], agent_name)
    _emit_task_event(writer, "task_started", task, agent_name, message="Task execution started", status_detail="Task execution started")

    if agent_name in ("SYSTEM_FINISH", "SYSTEM_FALLBACK"):
        return _handle_system_special(agent_name, task, state, writer)

    await _ensure_mcp_ready(agent_name)

    agent_config_override = RunnableConfig(
        configurable={
            **config.get("configurable", {}),
            "agent_name": agent_name,
            "subagent_enabled": False,
            "is_domain_agent": True,
        }
    )

    from src.agents.lead_agent.agent import make_lead_agent

    domain_agent = make_lead_agent(agent_config_override)
    clarification_answer = _extract_latest_clarification_answer(state)
    context = _build_context(task, state.get("verified_facts") or {}, clarification_answer)
    _emit_task_event(
        writer,
        "task_running",
        task,
        agent_name,
        message="Dispatching task to domain agent",
        status_detail="Dispatching task to domain agent",
        clarification_answer=clarification_answer or None,
    )

    try:
        result = await domain_agent.ainvoke(
            {"messages": [HumanMessage(content=context)]},
            config=agent_config_override,
        )
        messages = result.get("messages") or []

        if messages and isinstance(messages[-1], ToolMessage) and messages[-1].name == "ask_clarification":
            logger.info("[Executor] Task '%s' interrupted by ask_clarification.", task["task_id"])
            clarification_prompt = _content_to_text(messages[-1].content)
            interrupted_task: TaskStatus = {
                **task,
                "status": "RUNNING",
                "status_detail": "Waiting for user clarification",
                "clarification_prompt": clarification_prompt,
                "updated_at": _utc_now_iso(),
            }  # type: ignore[typeddict-item]
            _emit_task_event(
                writer,
                "task_running",
                task,
                agent_name,
                message="Waiting for user clarification",
                clarification_prompt=clarification_prompt,
                status="waiting_clarification",
                status_detail="Waiting for user clarification",
            )
            return {
                "task_pool": [interrupted_task],
                "execution_state": "INTERRUPTED",
                "messages": [AIMessage(content=clarification_prompt, name="ask_clarification")],
            }

        agent_output = _extract_agent_output(messages)
        if not agent_output:
            raise RuntimeError("Domain agent returned no final answer.")

        logger.info("[Executor] Task '%s' DONE. Output length=%d.", task["task_id"], len(agent_output))
        done_task: TaskStatus = {
            **task,
            "status": "DONE",
            "result": agent_output,
            "status_detail": "Task completed",
            "clarification_prompt": None,
            "updated_at": _utc_now_iso(),
        }  # type: ignore[typeddict-item]
        _emit_task_event(writer, "task_completed", task, agent_name, result=agent_output, status_detail="Task completed")
        return {
            "task_pool": [done_task],
            "verified_facts": {
                task["task_id"]: {
                    "agent": agent_name,
                    "task": task["description"],
                    "summary": agent_output,
                }
            },
            "execution_state": "EXECUTING_DONE",
        }
    except Exception as e:
        logger.error("[Executor] Task '%s' FAILED: %s", task["task_id"], e, exc_info=True)
        failed_task: TaskStatus = {
            **task,
            "status": "FAILED",
            "error": str(e),
            "status_detail": str(e),
            "clarification_prompt": None,
            "updated_at": _utc_now_iso(),
        }  # type: ignore[typeddict-item]
        _emit_task_event(writer, "task_failed", task, agent_name, error=str(e), status_detail=str(e))
        return {
            "task_pool": [failed_task],
            "execution_state": "EXECUTING_DONE",
        }
