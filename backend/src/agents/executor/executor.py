"""Domain Agent executor node for the multi-agent graph."""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.thread_state import (
    HelpRequestPayload,
    TaskStatus,
    ThreadState,
    VerifiedFact,
    WorkflowStage,
)
from src.agents.workflow_resume import extract_latest_clarification_answer
from src.config.agents_config import load_agent_config

logger = logging.getLogger(__name__)

_mcp_initialized: set[str] = set()
_NULLISH_TEXT_VALUES = {"none", "null", "undefined"}
SYSTEM_FALLBACK_FINAL_MESSAGE = "当前系统暂时无法处理该类问题，后续可按需扩展相关能力。"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _normalize_text_value(value: Any) -> str:
    if value is None:
        return ""
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if text.lower() in _NULLISH_TEXT_VALUES:
        return ""
    return text

def _fact_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        summary = value.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary
        result = value.get("result")
        if isinstance(result, str) and result.strip():
            return result
    return str(value)


def _json_block(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def _build_context(task: TaskStatus, verified_facts: VerifiedFact, clarification_answer: str = "") -> str:
    context = task["description"]

    if verified_facts:
        facts_block = "\n".join(
            f"  {i + 1}. [{fact_key}] {_fact_value_to_text(fact_value)}"
            for i, (fact_key, fact_value) in enumerate(verified_facts.items())
        )
        context += f"\n\nKnown facts (do not re-check):\n{facts_block}"

    resolved_inputs = task.get("resolved_inputs")
    if resolved_inputs:
        context += f"\n\nResolved dependency inputs:\n{_json_block(resolved_inputs)}"

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
    if event_type in {"task_started", "task_running", "task_resumed"}:
        return "in_progress"
    if event_type in {"task_waiting_dependency", "task_help_requested"}:
        return "waiting_dependency"
    if event_type == "task_completed":
        return "completed"
    if event_type == "task_failed":
        return "failed"
    return "unknown"


def _resolve_task_run_id(state: ThreadState, task: TaskStatus) -> str:
    return task.get("run_id") or state.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"


def _build_workflow_stage_update(
    stage: WorkflowStage | None,
    detail: str | None = None,
) -> dict:
    return {
        "workflow_stage": stage,
        "workflow_stage_detail": detail,
        "workflow_stage_updated_at": _utc_now_iso(),
    }


def _emit_workflow_stage(
    writer: Callable[[dict[str, Any]], None],
    stage: WorkflowStage,
    detail: str | None = None,
    *,
    run_id: str | None = None,
) -> None:
    writer(
        {
            "type": "workflow_stage_changed",
            "run_id": run_id,
            **_build_workflow_stage_update(stage, detail),
        }
    )


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
    writer(payload)


def _handle_system_special(agent_name: str, task: TaskStatus, state: ThreadState, writer: Callable[[dict[str, Any]], None]) -> dict:
    run_id = _resolve_task_run_id(state, task)
    if agent_name == "SYSTEM_FINISH":
        final = _get_latest_fact_text(state)
        done_task: TaskStatus = {
            **task,
            "run_id": run_id,
            "status": "DONE",
            "result": final,
            "status_detail": "Task completed by system shortcut",
            "clarification_prompt": None,
            "request_help": None,
            "blocked_reason": None,
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_completed", done_task, agent_name, result=final)
        _emit_workflow_stage(writer, "summarizing", final, run_id=run_id)
        return {
            "task_pool": [done_task],
            "execution_state": "DONE",
            "final_result": final,
            "run_id": run_id,
            "messages": [AIMessage(content=final)],
            **_build_workflow_stage_update("summarizing", final),
        }

    fallback_task: TaskStatus = {
        **task,
        "run_id": run_id,
        "status": "DONE",
        "result": SYSTEM_FALLBACK_FINAL_MESSAGE,
        "status_detail": "Completed by system fallback",
        "clarification_prompt": None,
        "request_help": None,
        "blocked_reason": None,
        "updated_at": _utc_now_iso(),
    }
    _emit_task_event(
        writer,
        "task_completed",
        fallback_task,
        agent_name,
        result=SYSTEM_FALLBACK_FINAL_MESSAGE,
        status_detail="Completed by system fallback",
    )
    _emit_workflow_stage(
        writer,
        "summarizing",
        SYSTEM_FALLBACK_FINAL_MESSAGE,
        run_id=run_id,
    )
    return {
        "task_pool": [fallback_task],
        "execution_state": "DONE",
        "final_result": SYSTEM_FALLBACK_FINAL_MESSAGE,
        "run_id": run_id,
        "messages": [AIMessage(content=SYSTEM_FALLBACK_FINAL_MESSAGE)],
        **_build_workflow_stage_update("summarizing", SYSTEM_FALLBACK_FINAL_MESSAGE),
    }


def _extract_agent_output(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        if not isinstance(message, AIMessage):
            continue
        text = _content_to_text(getattr(message, "content", ""))
        if text.strip():
            return text.strip()
    return ""


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_fact_payload(agent_output: str) -> dict[str, Any]:
    parsed = _parse_json_object(agent_output)
    if parsed is not None:
        return parsed
    return {"text": agent_output}


def _summarize_messages_for_log(messages: list[Any], *, limit: int = 8) -> str:
    if not messages:
        return "[]"

    tail = messages[-limit:]
    parts: list[str] = []
    start_index = max(len(messages) - len(tail), 0)
    for offset, message in enumerate(tail, start=start_index):
        message_type = message.__class__.__name__
        message_name = getattr(message, "name", None) or "-"
        preview = _content_to_text(getattr(message, "content", ""))[:120].replace("\n", "\\n")
        parts.append(f"{offset}:{message_type}:{message_name}:{preview}")
    if len(messages) > limit:
        return f"[... {len(messages) - limit} earlier omitted ...; {' | '.join(parts)}]"
    return f"[{' | '.join(parts)}]"


def _find_last_terminal_tool_signal(messages: list[Any]) -> tuple[int, ToolMessage] | None:
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", None) in {"request_help", "ask_clarification"}:
            return idx, message
    return None


def _parse_request_help_message(message: ToolMessage) -> HelpRequestPayload | None:
    raw_content = _content_to_text(message.content)
    payload = _parse_json_object(raw_content)
    if payload is None:
        logger.error("[Executor] request_help payload is not a valid JSON object: %r", raw_content[:2000])
        return None

    problem = _normalize_text_value(payload.get("problem", ""))
    required_capability = _normalize_text_value(payload.get("required_capability", ""))
    reason = _normalize_text_value(payload.get("reason", ""))
    expected_output = _normalize_text_value(payload.get("expected_output", ""))
    if not (problem and required_capability and reason and expected_output):
        logger.error(
            "[Executor] request_help payload missing required fields: %s",
            json.dumps(payload, ensure_ascii=False)[:2000],
        )
        return None

    result: HelpRequestPayload = {
        "problem": problem,
        "required_capability": required_capability,
        "reason": reason,
        "expected_output": expected_output,
    }
    resolution_strategy = _normalize_text_value(payload.get("resolution_strategy"))
    if resolution_strategy:
        result["resolution_strategy"] = resolution_strategy
    clarification_question = _normalize_text_value(payload.get("clarification_question"))
    if clarification_question:
        result["clarification_question"] = clarification_question
    clarification_context = _normalize_text_value(payload.get("clarification_context"))
    if clarification_context:
        result["clarification_context"] = clarification_context
    clarification_options = payload.get("clarification_options")
    if isinstance(clarification_options, str):
        text = clarification_options.strip()
        if text.startswith("["):
            try:
                clarification_options = json.loads(text)
            except Exception:
                clarification_options = clarification_options
    if isinstance(clarification_options, list):
        options = [_normalize_text_value(item) for item in clarification_options]
        options = [option for option in options if option]
        if options:
            result["clarification_options"] = options
    context_payload = payload.get("context_payload")
    if isinstance(context_payload, dict):
        result["context_payload"] = context_payload
    candidate_agents = payload.get("candidate_agents")
    if isinstance(candidate_agents, str):
        text = candidate_agents.strip()
        if text.startswith("["):
            try:
                candidate_agents = json.loads(text)
            except Exception:
                candidate_agents = candidate_agents
    if isinstance(candidate_agents, list):
        agents = [_normalize_text_value(item) for item in candidate_agents]
        agents = [agent for agent in agents if agent]
        if agents:
            result["candidate_agents"] = agents
    logger.info("[Executor] Parsed request_help payload: %s", json.dumps(result, ensure_ascii=False)[:2000])
    return result


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
    }
    agent_name = task.get("assigned_agent") or "SYSTEM_FALLBACK"
    writer = _get_event_writer()
    logger.info("[Executor] Executing task '%s' via agent '%s'.", task["task_id"], agent_name)
    _emit_task_event(writer, "task_started", task, agent_name, message="Task execution started", status_detail="Task execution started")

    if agent_name in ("SYSTEM_FINISH", "SYSTEM_FALLBACK"):
        return _handle_system_special(agent_name, task, state, writer)

    try:
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
        clarification_answer = extract_latest_clarification_answer(state)
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

        result = await domain_agent.ainvoke(
            {"messages": [HumanMessage(content=context)]},
            config=agent_config_override,
        )
        messages = result.get("messages") or []
        logger.info(
            "[Executor] Agent '%s' returned %d message(s); last_type=%s last_name=%s",
            agent_name,
            len(messages),
            messages[-1].__class__.__name__ if messages else None,
            getattr(messages[-1], "name", None) if messages else None,
        )
        logger.info(
            "[Executor] Task '%s' message trace: %s",
            task["task_id"],
            _summarize_messages_for_log(messages),
        )

        terminal_tool_signal = _find_last_terminal_tool_signal(messages)
        if terminal_tool_signal and terminal_tool_signal[1].name == "request_help":
            terminal_idx, terminal_message = terminal_tool_signal
            if terminal_idx != len(messages) - 1:
                logger.warning(
                    "[Executor] Agent '%s' emitted request_help at index %d/%d; honoring it as the terminal signal "
                    "even though trailing messages were present.",
                    agent_name,
                    terminal_idx,
                    len(messages) - 1,
                )
            logger.info(
                "[Executor] Agent '%s' emitted request_help raw content: %s",
                agent_name,
                _content_to_text(terminal_message.content)[:2000],
            )
            help_request = _parse_request_help_message(terminal_message)
            if help_request is None:
                raise RuntimeError("request_help returned invalid structured payload.")

            next_help_depth = int(task.get("help_depth") or 0) + 1
            waiting_task: TaskStatus = {
                **task,
                "status": "WAITING_DEPENDENCY",
                "requested_by_agent": agent_name,
                "request_help": help_request,
                "blocked_reason": help_request["reason"],
                "help_depth": next_help_depth,
                "clarification_prompt": None,
                "status_detail": "Waiting for router to resolve dependency",
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(
                writer,
                "task_waiting_dependency",
                waiting_task,
                agent_name,
                blocked_reason=help_request["reason"],
                request_help=help_request,
                requested_by_agent=agent_name,
                status_detail="Waiting for router to resolve dependency",
            )
            _emit_task_event(
                writer,
                "task_help_requested",
                waiting_task,
                agent_name,
                request_help=help_request,
                requested_by_agent=agent_name,
                status_detail=help_request["required_capability"],
            )
            return {
                "task_pool": [waiting_task],
                "execution_state": "EXECUTING_DONE",
            }

        if terminal_tool_signal and terminal_tool_signal[1].name == "ask_clarification":
            terminal_idx, terminal_message = terminal_tool_signal
            if terminal_idx != len(messages) - 1:
                logger.warning(
                    "[Executor] Task '%s' emitted ask_clarification at index %d/%d; honoring it as the terminal signal "
                    "even though trailing messages were present.",
                    task["task_id"],
                    terminal_idx,
                    len(messages) - 1,
                )
            logger.info("[Executor] Task '%s' interrupted by ask_clarification.", task["task_id"])
            clarification_prompt = _content_to_text(terminal_message.content)
            interrupted_task: TaskStatus = {
                **task,
                "status": "RUNNING",
                "status_detail": "Waiting for user clarification",
                "clarification_prompt": clarification_prompt,
                "request_help": None,
                "blocked_reason": None,
                "updated_at": _utc_now_iso(),
            }
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

        logger.info(
            "[Executor] Task '%s' via agent '%s' has no terminal tool signal; treating latest AI text as final output. "
            "output_preview=%r",
            task["task_id"],
            agent_name,
            agent_output[:300],
        )

        logger.info("[Executor] Task '%s' DONE. Output length=%d.", task["task_id"], len(agent_output))
        logger.info("[Executor] Agent '%s' final output: %s", agent_name, agent_output[:2000])
        payload = _normalize_fact_payload(agent_output)
        done_task: TaskStatus = {
            **task,
            "status": "DONE",
            "result": agent_output,
            "status_detail": "Task completed",
            "clarification_prompt": None,
            "request_help": None,
            "blocked_reason": None,
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_completed", task, agent_name, result=agent_output, status_detail="Task completed")
        return {
            "task_pool": [done_task],
            "verified_facts": {
                task["task_id"]: {
                    "agent": agent_name,
                    "task": task["description"],
                    "summary": agent_output,
                    "payload": payload,
                    "fact_type": "task_result",
                    "source_task_id": task["task_id"],
                    "updated_at": _utc_now_iso(),
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
            "request_help": None,
            "blocked_reason": None,
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_failed", task, agent_name, error=str(e), status_detail=str(e))
        return {
            "task_pool": [failed_task],
            "execution_state": "EXECUTING_DONE",
        }
