"""Domain Agent executor node for the multi-agent graph."""

import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.thread_state import (
    HelpRequestPayload,
    InterventionRequest,
    InterventionResolution,
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
_IMPLICIT_CLARIFICATION_MARKERS = (
    "请选择",
    "请确认",
    "请提供",
    "请补充",
    "请告知",
    "please choose",
    "please confirm",
    "please provide",
    "which",
)
_COMPLETION_TEXT_MARKERS = (
    "已预定",
    "预定成功",
    "已预约",
    "booked",
    "confirmed",
    "created successfully",
)
_NUMBERED_OPTION_PATTERN = re.compile(r"(?m)^\s*\d+[\.\)]\s+\S+")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    # For helper tasks, use the full technical context instead of the short display description
    helper_context = task.get("helper_context")
    context = helper_context if helper_context else task["description"]

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
        return "任务已完成。"
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
    if event_type == "task_waiting_intervention":
        return "waiting_intervention"
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
            "status_detail": "@completed",
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
        "status_detail": "@completed_fallback",
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
        status_detail="@completed_fallback",
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


def _contains_choice_enumeration(text: str) -> bool:
    lowered = text.lower()
    if len(_NUMBERED_OPTION_PATTERN.findall(text)) >= 2:
        return True
    if "或" in text and any(separator in text for separator in ("、", "，", ",")):
        return True
    return " or " in lowered and "," in lowered


def _looks_like_implicit_clarification(agent_output: str) -> bool:
    text = agent_output.strip()
    if not text:
        return False
    if _parse_json_object(text) is not None:
        return False

    lowered = text.lower()
    if any(marker in text for marker in _COMPLETION_TEXT_MARKERS[:3]):
        return False
    if any(marker in lowered for marker in _COMPLETION_TEXT_MARKERS[3:]):
        return False

    has_question_signal = any(marker in text for marker in _IMPLICIT_CLARIFICATION_MARKERS[:4])
    has_question_signal = has_question_signal or any(marker in lowered for marker in _IMPLICIT_CLARIFICATION_MARKERS[4:])
    has_question_signal = has_question_signal or "?" in text or "？" in text

    return has_question_signal or _contains_choice_enumeration(text)


def _log_executor_decision(
    task: TaskStatus,
    agent_name: str,
    messages: list[Any],
    classification: str,
    **extra: Any,
) -> None:
    last_message = messages[-1] if messages else None
    payload = {
        "run_id": task.get("run_id"),
        "task_id": task["task_id"],
        "agent_name": agent_name,
        "task_description": task["description"][:200],
        "message_count": len(messages),
        "last_message_type": last_message.__class__.__name__ if last_message else None,
        "last_message_name": getattr(last_message, "name", None) if last_message else None,
        "classification": classification,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    level = logging.WARNING if classification == "implicit_clarification" else logging.INFO
    logger.log(level, "[Executor] Decision trace: %s", json.dumps(payload, ensure_ascii=False, default=str)[:4000])


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
        if getattr(message, "name", None) in {"request_help", "ask_clarification", "intervention_required"}:
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


def _parse_intervention_required_message(message: ToolMessage) -> InterventionRequest | None:
    """Parse an intervention_required ToolMessage into an InterventionRequest."""
    raw_content = _content_to_text(message.content)
    payload = _parse_json_object(raw_content)
    if payload is None:
        logger.error("[Executor] intervention_required payload is not valid JSON: %r", raw_content[:2000])
        return None

    # Validate required fields
    required = ("request_id", "fingerprint", "intervention_type", "title", "reason", "source_agent", "source_task_id", "action_schema", "created_at")
    missing = [f for f in required if f not in payload]
    if missing:
        logger.error("[Executor] intervention_required payload missing fields: %s", missing)
        return None

    return payload  # type: ignore[return-value]


def _collect_resolved_fingerprints(task_pool: list[TaskStatus]) -> set[str]:
    """Collect fingerprints of already-resolved interventions in the current run."""
    fingerprints: set[str] = set()
    for task in task_pool:
        resolution = task.get("intervention_resolution")
        if isinstance(resolution, dict):
            fp = resolution.get("fingerprint")
            if fp:
                fingerprints.add(fp)
        # Also collect consumed fingerprints
        if task.get("intervention_status") in ("resolved", "consumed"):
            fp = task.get("intervention_fingerprint")
            if fp:
                fingerprints.add(fp)
    return fingerprints


def _serialize_agent_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Serialize LangChain messages to dicts for checkpoint persistence."""
    try:
        return messages_to_dict(messages)
    except Exception as e:
        logger.warning("[Executor] Failed to serialize agent messages: %s", e)
        return []


def _deserialize_agent_messages(data: list[dict[str, Any]] | None) -> list[Any]:
    """Deserialize persisted message dicts back to LangChain messages."""
    if not data:
        return []
    try:
        return messages_from_dict(data)
    except Exception as e:
        logger.warning("[Executor] Failed to deserialize agent messages, starting fresh: %s", e)
        return []


def _extract_intercepted_tool_call(messages: list[Any]) -> dict[str, Any] | None:
    """Extract the original tool call that was intercepted by intervention middleware.

    Looks for the AIMessage containing the tool_call that produced the
    intervention_required ToolMessage.
    """
    # Find the intervention_required ToolMessage
    intervention_tool_call_id = None
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage) and getattr(msg, "name", None) == "intervention_required":
            intervention_tool_call_id = getattr(msg, "tool_call_id", None)
            break

    if not intervention_tool_call_id:
        return None

    # Find the AIMessage with that tool_call
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        for tc in (msg.tool_calls or []):
            if tc.get("id") == intervention_tool_call_id:
                return {
                    "tool_call_id": tc["id"],
                    "tool_name": tc["name"],
                    "tool_args": tc.get("args", {}),
                }
    return None


async def _execute_intercepted_tool_call(
    intercepted: dict[str, Any],
    agent_config: RunnableConfig,
) -> ToolMessage:
    """Execute a previously intercepted tool call directly, bypassing the agent."""
    tool_name = intercepted["tool_name"]
    tool_args = intercepted["tool_args"]
    tool_call_id = intercepted["tool_call_id"]

    agent_name = agent_config.get("configurable", {}).get("agent_name", "")

    # Load tools for this agent
    from src.config.agents_config import load_agent_config as _load_agent_cfg
    agent_cfg = _load_agent_cfg(agent_name) if agent_name else None
    mcp_servers = []
    if agent_cfg and agent_cfg.mcp_servers:
        mcp_servers = [s.model_dump() for s in agent_cfg.mcp_servers]

    # Get MCP tools from pool
    tools = []
    if mcp_servers:
        try:
            from src.execution.mcp_pool import mcp_pool
            tools = await mcp_pool.get_agent_tools(agent_name) or []
        except Exception:
            pass

    # Find the matching tool
    target_tool = None
    for tool in tools:
        if getattr(tool, "name", None) == tool_name:
            target_tool = tool
            break

    if target_tool is None:
        return ToolMessage(
            content=json.dumps({"error": f"Tool '{tool_name}' not found for fast-path execution."}, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=tool_name,
        )

    try:
        result = await target_tool.ainvoke(tool_args)
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        content = json.dumps({"error": str(e)}, ensure_ascii=False)

    return ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)


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
    _emit_task_event(writer, "task_started", task, agent_name, message="Task execution started", status_detail="@task_started")

    if agent_name in ("SYSTEM_FINISH", "SYSTEM_FALLBACK"):
        return _handle_system_special(agent_name, task, state, writer)

    try:
        await _ensure_mcp_ready(agent_name)

        # Collect resolved fingerprints for dedup in InterventionMiddleware
        resolved_fingerprints = _collect_resolved_fingerprints(task_pool)

        # Load agent config for intervention policies
        try:
            agent_cfg = load_agent_config(agent_name)
        except Exception:
            agent_cfg = None
        intervention_policies = {}
        hitl_keywords: list[str] = []
        if agent_cfg:
            intervention_policies = getattr(agent_cfg, "intervention_policies", None) or {}
            hitl_keywords = getattr(agent_cfg, "hitl_keywords", None) or []

        agent_config_override = RunnableConfig(
            configurable={
                **config.get("configurable", {}),
                "agent_name": agent_name,
                "subagent_enabled": False,
                "is_domain_agent": True,
                "run_id": task_run_id,
                "task_id": task["task_id"],
                "intervention_policies": intervention_policies,
                "hitl_keywords": hitl_keywords,
                "resolved_fingerprints": resolved_fingerprints,
            }
        )

        from src.agents.lead_agent.agent import make_lead_agent

        clarification_answer = extract_latest_clarification_answer(state)
        context = _build_context(task, state.get("verified_facts") or {}, clarification_answer)

        # Check for intervention fast-path: if we have a stored tool call and an
        # approve resolution, execute the tool directly without re-invoking the agent.
        intercepted = task.get("intercepted_tool_call")
        # resolution_behavior is stored in resolved_inputs["intervention_resolution"],
        # NOT in the top-level task.intervention_resolution (which is InterventionResolution type).
        resolved_intervention = (task.get("resolved_inputs") or {}).get("intervention_resolution")
        is_intervention_fast_path = (
            intercepted is not None
            and isinstance(resolved_intervention, dict)
            and resolved_intervention.get("resolution_behavior") == "resume_current_task"
        )

        logger.info(
            "[Executor] Dispatch context: %s",
            json.dumps(
                {
                    "run_id": task_run_id,
                    "task_id": task["task_id"],
                    "agent_name": agent_name,
                    "task_description": task["description"][:200],
                    "resolved_input_keys": list((task.get("resolved_inputs") or {}).keys()),
                    "clarification_answer_present": bool(clarification_answer),
                    "intervention_fast_path": is_intervention_fast_path,
                    "has_agent_history": bool(task.get("agent_messages")),
                },
                ensure_ascii=False,
            ),
        )
        logger.info(
            "[Executor] Resume diagnostics run_id=%s task_id=%s assigned_agent=%s clarification_answer=%r "
            "resolved_inputs=%s messages_count=%s latest_message_type=%s",
            task_run_id,
            task["task_id"],
            agent_name,
            clarification_answer,
            json.dumps(task.get("resolved_inputs") or {}, ensure_ascii=False),
            len(state.get("messages") or []),
            state.get("messages")[-1].__class__.__name__ if state.get("messages") else None,
        )
        _emit_task_event(
            writer,
            "task_running",
            task,
            agent_name,
            message="Dispatching task to domain agent",
            status_detail="@dispatching",
            clarification_answer=clarification_answer or None,
        )

        if is_intervention_fast_path:
            # --- Intervention fast-path: execute the stored tool call directly ---
            logger.info(
                "[Executor] Intervention fast-path: executing intercepted tool '%s' for task '%s'.",
                intercepted["tool_name"],
                task["task_id"],
            )
            await _ensure_mcp_ready(agent_name)
            tool_result_message = await _execute_intercepted_tool_call(intercepted, agent_config_override)

            # Restore previous agent messages, truncate from the intervention_required
            # ToolMessage onward (the agent may have generated trailing AIMessages after
            # seeing the intervention response), then append the real tool result.
            prior_messages = _deserialize_agent_messages(task.get("agent_messages"))
            truncate_idx = None
            for idx, msg in enumerate(prior_messages):
                if isinstance(msg, ToolMessage) and getattr(msg, "name", None) == "intervention_required":
                    truncate_idx = idx
                    break
            if truncate_idx is not None:
                prior_messages = prior_messages[:truncate_idx]
            prior_messages.append(tool_result_message)

            # Let the agent continue from where it left off with the tool result
            domain_agent = make_lead_agent(agent_config_override)
            result = await domain_agent.ainvoke(
                {"messages": prior_messages},
                config=agent_config_override,
            )
            messages = result.get("messages") or []
        else:
            # --- Normal path: invoke domain agent ---
            # Restore prior agent conversation history if available (avoids
            # redundant tool calls on clarification/dependency resume).
            prior_messages = _deserialize_agent_messages(task.get("agent_messages"))
            if prior_messages:
                # Strip intervention_required ToolMessage and any trailing messages
                # (should not happen here but be safe)
                for _idx, _msg in enumerate(prior_messages):
                    if isinstance(_msg, ToolMessage) and getattr(_msg, "name", None) == "intervention_required":
                        prior_messages = prior_messages[:_idx]
                        break
                # Append the new context as a follow-up HumanMessage
                input_messages = prior_messages + [HumanMessage(content=context)]
                logger.info(
                    "[Executor] Resuming with %d prior messages + new context for task '%s'.",
                    len(prior_messages),
                    task["task_id"],
                )
            else:
                input_messages = [HumanMessage(content=context)]

            domain_agent = make_lead_agent(agent_config_override)
            result = await domain_agent.ainvoke(
                {"messages": input_messages},
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
        terminal_signal_name = terminal_tool_signal[1].name if terminal_tool_signal else None

        # --- intervention_required handling ---
        if terminal_tool_signal and terminal_tool_signal[1].name == "intervention_required":
            terminal_idx, terminal_message = terminal_tool_signal
            logger.info("[Executor] Task '%s' interrupted by intervention_required.", task["task_id"])
            intervention_request = _parse_intervention_required_message(terminal_message)
            if intervention_request is None:
                raise RuntimeError("intervention_required returned invalid structured payload.")
            _log_executor_decision(
                task,
                agent_name,
                messages,
                "intervention_required",
                terminal_signal=terminal_signal_name,
                intervention_request_id=intervention_request["request_id"],
                intervention_fingerprint=intervention_request["fingerprint"],
            )
            # Save agent conversation history and intercepted tool call for resume
            serialized_messages = _serialize_agent_messages(messages)
            intercepted_tool = _extract_intercepted_tool_call(messages)
            intervention_task: TaskStatus = {
                **task,
                "status": "WAITING_INTERVENTION",
                "status_detail": "@waiting_intervention",
                "intervention_request": intervention_request,
                "intervention_status": "pending",
                "intervention_fingerprint": intervention_request["fingerprint"],
                "intervention_resolution": None,
                "clarification_prompt": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": serialized_messages,
                "intercepted_tool_call": intercepted_tool,
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(
                writer,
                "task_waiting_intervention",
                intervention_task,
                agent_name,
                status_detail="@waiting_intervention",
                intervention_request=intervention_request,
            )
            return {
                "task_pool": [intervention_task],
                "execution_state": "INTERRUPTED",
            }

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
            _log_executor_decision(
                task,
                agent_name,
                messages,
                "request_help",
                terminal_signal=terminal_signal_name,
                help_request_reason=help_request.get("reason"),
                help_request_expected_output=help_request.get("expected_output"),
            )

            next_help_depth = int(task.get("help_depth") or 0) + 1
            serialized_messages = _serialize_agent_messages(messages)
            waiting_task: TaskStatus = {
                **task,
                "status": "WAITING_DEPENDENCY",
                "requested_by_agent": agent_name,
                "request_help": help_request,
                "blocked_reason": help_request["reason"],
                "help_depth": next_help_depth,
                "clarification_prompt": None,
                "status_detail": "@waiting_dependency",
                "agent_messages": serialized_messages,
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
                status_detail="@waiting_dependency",
            )
            _emit_task_event(
                writer,
                "task_help_requested",
                waiting_task,
                agent_name,
                request_help=help_request,
                requested_by_agent=agent_name,
                status_detail="@waiting_dependency",
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
            _log_executor_decision(
                task,
                agent_name,
                messages,
                "ask_clarification",
                terminal_signal=terminal_signal_name,
                clarification_prompt=clarification_prompt[:300],
            )
            serialized_messages = _serialize_agent_messages(messages)
            interrupted_task: TaskStatus = {
                **task,
                "status": "RUNNING",
                "status_detail": "@waiting_clarification",
                "clarification_prompt": clarification_prompt,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": serialized_messages,
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(
                writer,
                "task_running",
                task,
                agent_name,
                message="需要你补充信息",
                clarification_prompt=clarification_prompt,
                status="waiting_clarification",
                status_detail="@waiting_clarification",
            )
            return {
                "task_pool": [interrupted_task],
                "execution_state": "INTERRUPTED",
                "messages": [AIMessage(content=clarification_prompt, name="ask_clarification")],
            }

        agent_output = _extract_agent_output(messages)
        if not agent_output:
            raise RuntimeError("Domain agent returned no final answer.")

        if _looks_like_implicit_clarification(agent_output):
            _log_executor_decision(
                task,
                agent_name,
                messages,
                "implicit_clarification",
                terminal_signal=terminal_signal_name,
                clarification_prompt=agent_output[:300],
            )
            serialized_messages = _serialize_agent_messages(messages)
            interrupted_task: TaskStatus = {
                **task,
                "status": "RUNNING",
                "status_detail": "@waiting_clarification",
                "clarification_prompt": agent_output,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": serialized_messages,
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(
                writer,
                "task_running",
                interrupted_task,
                agent_name,
                message="Waiting for user clarification inferred from plain-text output",
                clarification_prompt=agent_output,
                status="waiting_clarification",
                status_detail="@waiting_clarification",
            )
            return {
                "task_pool": [interrupted_task],
                "execution_state": "INTERRUPTED",
                "messages": [AIMessage(content=agent_output, name="ask_clarification")],
            }

        _log_executor_decision(
            task,
            agent_name,
            messages,
            "final_output",
            terminal_signal=terminal_signal_name,
            final_output_preview=agent_output[:300],
        )
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
            "status_detail": "@completed",
            "clarification_prompt": None,
            "request_help": None,
            "blocked_reason": None,
            "agent_messages": None,
            "intercepted_tool_call": None,
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_completed", task, agent_name, result=agent_output, status_detail="@completed")
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
            "status_detail": "@failed",
            "clarification_prompt": None,
            "request_help": None,
            "blocked_reason": None,
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_failed", task, agent_name, error=str(e), status_detail="@failed")
        return {
            "task_pool": [failed_task],
            "execution_state": "EXECUTING_DONE",
        }
