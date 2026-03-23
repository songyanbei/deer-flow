"""Domain Agent executor node for the multi-agent graph."""

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.intervention.decision_cache import (
    DEFAULT_CLARIFICATION_MAX_REUSE,
    increment_cache_reuse_count,
    is_intervention_cache_valid,
)
from src.agents.intervention.fingerprint import (
    generate_clarification_semantic_fingerprint,
    generate_tool_snapshot_hash,
)
from src.agents.executor.outcome import normalize_agent_outcome
from src.agents.thread_state import (
    HelpRequestPayload,
    InterventionRequest,
    InterventionResolution,
    PendingToolCall,
    TaskStatus,
    ThreadState,
    VerifiedFact,
    WorkflowStage,
)
from src.agents.workflow_resume import (
    build_intervention_resolution_record,
    build_intervention_resolved_inputs_entry,
    extract_latest_clarification_answer,
)
from src.config.agents_config import load_agent_config
from src.observability import record_decision

logger = logging.getLogger(__name__)

_mcp_initialized: set[str] = set()
_NULLISH_TEXT_VALUES = {"none", "null", "undefined"}
SYSTEM_FALLBACK_FINAL_MESSAGE = "当前系统暂时无法处理该类问题，后续可按需扩展相关能力。"


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
    if agent_cfg and getattr(agent_cfg, "mcp_binding", None):
        try:
            from src.config.extensions_config import ExtensionsConfig
            from src.mcp.binding_resolver import resolve_binding
            from src.mcp.runtime_manager import mcp_runtime

            binding = agent_cfg.get_effective_mcp_binding()
            extensions_config = ExtensionsConfig.from_file()
            resolved_servers = resolve_binding(binding, extensions_config)

            if resolved_servers:
                scope_key = mcp_runtime.scope_key_for_agent(agent_name)
                success = await mcp_runtime.load_scope(scope_key, resolved_servers)
                if not success:
                    error = mcp_runtime.get_scope_error(scope_key) or "unknown MCP connection error"
                    raise RuntimeError(error)
                logger.info("[Executor] MCP ready for agent '%s' (%d server(s)).", agent_name, len(resolved_servers))
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
            "clarification_request": None,
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
        "clarification_request": None,
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


def _interrupt_kind_from_request(intervention_request: InterventionRequest) -> str:
    explicit_kind = str(intervention_request.get("interrupt_kind") or "").strip()
    if explicit_kind:
        return explicit_kind

    intervention_type = str(intervention_request.get("intervention_type") or "").strip()
    if intervention_type == "before_tool":
        return "before_tool"

    actions = ((intervention_request.get("action_schema") or {}).get("actions") or [])
    first_action = actions[0] if actions else {}
    action_kind = str(first_action.get("kind") or "").strip()
    if action_kind in {"single_select", "multi_select", "select"}:
        return "selection"
    if action_kind in {"confirm", "button"}:
        return "confirmation"
    return "clarification"


def _build_pending_interrupt(
    *,
    interrupt_type: str,
    request_id: str | None = None,
    fingerprint: str | None = None,
    interrupt_kind: str | None = None,
    semantic_key: str | None = None,
    source_signal: str | None = None,
    source_agent: str | None = None,
    prompt: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    pending_interrupt: dict[str, Any] = {
        "interrupt_type": interrupt_type,
        "created_at": _utc_now_iso(),
    }
    if request_id:
        pending_interrupt["request_id"] = request_id
    if fingerprint:
        pending_interrupt["fingerprint"] = fingerprint
    if interrupt_kind:
        pending_interrupt["interrupt_kind"] = interrupt_kind
    if semantic_key:
        pending_interrupt["semantic_key"] = semantic_key
    if source_signal:
        pending_interrupt["source_signal"] = source_signal
    if source_agent:
        pending_interrupt["source"] = source_agent
        pending_interrupt["source_agent"] = source_agent
    if prompt:
        pending_interrupt["prompt"] = prompt
    if options:
        pending_interrupt["options"] = options
    return pending_interrupt


def _build_pending_tool_call(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    tool_call_id: str | None,
    idempotency_key: str | None,
    source_agent: str,
    source_task_id: str,
    interrupt_fingerprint: str | None,
) -> PendingToolCall:
    return {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_call_id": tool_call_id,
        "idempotency_key": idempotency_key,
        "source_agent": source_agent,
        "source_task_id": source_task_id,
        "snapshot_hash": generate_tool_snapshot_hash(tool_name, tool_args),
        "interrupt_fingerprint": interrupt_fingerprint,
    }


def _log_interrupt_event(event: str, **payload: Any) -> None:
    logger.info("[Executor] %s %s", event, json.dumps(payload, ensure_ascii=False, default=str)[:4000])


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
    """Execute a previously intercepted tool call directly, bypassing the agent.

    If the intercepted dict contains an ``idempotency_key`` it is logged for
    traceability.  The underlying tool layer does not yet consume the key, but
    it is available for future idempotent execution support.
    """
    tool_name = intercepted["tool_name"]
    tool_args = intercepted["tool_args"]
    tool_call_id = intercepted.get("tool_call_id") or ""
    idempotency_key = intercepted.get("idempotency_key")

    agent_name = agent_config.get("configurable", {}).get("agent_name", "")

    if idempotency_key:
        logger.info(
            "[Executor] Executing intercepted tool '%s' with idempotency_key=%s tool_call_id=%s",
            tool_name, idempotency_key, tool_call_id,
        )

    # Get MCP tools from runtime manager
    tools = []
    if agent_name:
        try:
            from src.mcp.runtime_manager import mcp_runtime

            scope_key = mcp_runtime.scope_key_for_agent(agent_name)
            tools = await mcp_runtime.get_tools(scope_key)
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


def _clear_continuation_fields() -> dict[str, Any]:
    """Return field overrides that clear all continuation state."""
    return {
        "continuation_mode": None,
        "pending_interrupt": None,
        "pending_tool_call": None,
    }


def _auto_resume_from_clarification_cache(
    task: TaskStatus,
    *,
    cached: dict[str, Any],
    semantic_fp: str,
    intervention_cache: dict[str, dict[str, Any]],
    serialized_messages: list[dict[str, Any]],
    next_help_depth: int,
    agent_name: str,
    writer: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    updated_entry = increment_cache_reuse_count(cached)
    updated_cache = {**intervention_cache, semantic_fp: updated_entry}

    cached_resolution = build_intervention_resolution_record(
        request_id=f"cache:{semantic_fp}",
        fingerprint=semantic_fp,
        action_key=updated_entry["action_key"],
        payload=updated_entry.get("payload", {}),
        resolution_behavior=updated_entry.get("resolution_behavior", "resume_current_task"),
    )
    resolved_inputs = dict(task.get("resolved_inputs") or {})
    resolved_inputs["intervention_resolution"] = build_intervention_resolved_inputs_entry(cached_resolution)

    resumed_task: TaskStatus = {
        **task,
        "status": "RUNNING",
        "requested_by_agent": agent_name,
        "request_help": None,
        "blocked_reason": None,
        "help_depth": next_help_depth,
        "clarification_prompt": None,
        "clarification_request": None,
        "intervention_resolution": cached_resolution,
        "resolved_inputs": resolved_inputs,
        "status_detail": "@cache_auto_resolved",
        "agent_messages": serialized_messages,
        "continuation_mode": "continue_after_intervention",
        "pending_interrupt": None,
        "pending_tool_call": None,
        "resume_count": int(task.get("resume_count") or 0) + 1,
        "updated_at": _utc_now_iso(),
    }
    _emit_task_event(
        writer,
        "task_resumed",
        resumed_task,
        agent_name,
        status_detail="@cache_auto_resolved",
        resolved_inputs=resumed_task.get("resolved_inputs"),
        resume_count=resumed_task.get("resume_count"),
    )
    return {
        "task_pool": [resumed_task],
        "execution_state": "ROUTING_DONE",
        "run_id": resumed_task.get("run_id"),
        "intervention_cache": updated_cache,
    }


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
    continuation_mode = task.get("continuation_mode")
    logger.info(
        "[Executor] Executing task '%s' via agent '%s' continuation_mode=%s.",
        task["task_id"], agent_name, continuation_mode,
    )
    _emit_task_event(writer, "task_started", task, agent_name, message="Task execution started", status_detail="@task_started")

    if agent_name in ("SYSTEM_FINISH", "SYSTEM_FALLBACK"):
        return _handle_system_special(agent_name, task, state, writer)

    try:
        intervention_cache = state.get("intervention_cache") or {}

        def _with_intervention_cache(payload: dict[str, Any]) -> dict[str, Any]:
            return {
                **payload,
                "intervention_cache": intervention_cache,
            }

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
                "intervention_cache": intervention_cache,
            }
        )

        from src.agents.lead_agent.agent import make_lead_agent

        clarification_answer = extract_latest_clarification_answer(state, config)
        context = _build_context(task, state.get("verified_facts") or {}, clarification_answer)

        # ---------------------------------------------------------------
        # Resume branch selection — prefer continuation_mode, fall back
        # to legacy intercepted_tool_call heuristic for old tasks.
        # ---------------------------------------------------------------
        # Phase 2: read pending_tool_call first, fall back to intercepted_tool_call
        stored_tool_call = task.get("pending_tool_call") or task.get("intercepted_tool_call")
        resolved_intervention = (task.get("resolved_inputs") or {}).get("intervention_resolution")

        is_resume_tool_call = continuation_mode == "resume_tool_call"
        # Legacy fallback: old tasks without continuation_mode
        if not is_resume_tool_call and continuation_mode is None:
            is_resume_tool_call = (
                stored_tool_call is not None
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
                    "continuation_mode": continuation_mode,
                    "is_resume_tool_call": is_resume_tool_call,
                    "has_agent_history": bool(task.get("agent_messages")),
                },
                ensure_ascii=False,
            ),
        )
        logger.info(
            "[Executor] Resume diagnostics run_id=%s task_id=%s assigned_agent=%s clarification_answer=%r "
            "continuation_mode=%s resolved_inputs=%s messages_count=%s latest_message_type=%s",
            task_run_id,
            task["task_id"],
            agent_name,
            clarification_answer,
            continuation_mode,
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

        # ---------------------------------------------------------------
        # Execution: select branch based on continuation_mode
        # ---------------------------------------------------------------
        new_messages_start: int = 0

        if is_resume_tool_call and stored_tool_call:
            # --- resume_tool_call: execute the stored tool call directly ---
            # Dedup guard: reject if intervention was already consumed
            if task.get("intervention_status") == "consumed":
                idem_key = stored_tool_call.get("idempotency_key", "?")
                logger.warning(
                    "[Executor] Duplicate resume rejected for task '%s' — intervention already consumed (idempotency_key=%s).",
                    task["task_id"], idem_key,
                )
                return _with_intervention_cache({
                    "task_pool": [task],
                    "execution_state": "EXECUTING_DONE",
                })

            if task.get("intervention_status") != "resolved":
                raise RuntimeError(
                    f"resume_tool_call requires resolved intervention, got status={task.get('intervention_status')!r}"
                )

            expected_snapshot = stored_tool_call.get("snapshot_hash")
            actual_snapshot = generate_tool_snapshot_hash(
                stored_tool_call["tool_name"],
                stored_tool_call.get("tool_args", {}),
            )
            if expected_snapshot and expected_snapshot != actual_snapshot:
                raise RuntimeError("pending_tool_call snapshot drift detected before resume_tool_call")

            resolved_fingerprint = str((resolved_intervention or {}).get("fingerprint") or "")
            pending_fingerprint = str(stored_tool_call.get("interrupt_fingerprint") or "")
            if resolved_fingerprint and pending_fingerprint and resolved_fingerprint != pending_fingerprint:
                raise RuntimeError("resolved intervention fingerprint does not match pending_tool_call binding")

            logger.info(
                "[Executor] resume_tool_call: executing intercepted tool '%s' for task '%s' idempotency_key=%s.",
                stored_tool_call["tool_name"],
                task["task_id"],
                stored_tool_call.get("idempotency_key"),
            )
            _log_interrupt_event(
                "interrupt_consuming",
                run_id=task_run_id,
                task_id=task["task_id"],
                agent_name=agent_name,
                request_id=(resolved_intervention or {}).get("request_id"),
                fingerprint=resolved_fingerprint or pending_fingerprint,
                tool_name=stored_tool_call["tool_name"],
            )
            await _ensure_mcp_ready(agent_name)
            tool_result_message = await _execute_intercepted_tool_call(stored_tool_call, agent_config_override)

            # Mark intervention as consumed to prevent duplicate execution
            task = {**task, "intervention_status": "consumed"}
            _log_interrupt_event(
                "interrupt_consumed",
                run_id=task_run_id,
                task_id=task["task_id"],
                agent_name=agent_name,
                request_id=(resolved_intervention or {}).get("request_id"),
                fingerprint=resolved_fingerprint or pending_fingerprint,
                tool_name=stored_tool_call["tool_name"],
            )

            # Restore previous agent messages, truncate from the intervention_required
            # ToolMessage onward, then append the real tool result.
            prior_messages = _deserialize_agent_messages(task.get("agent_messages"))
            truncate_idx = None
            for idx, msg in enumerate(prior_messages):
                if isinstance(msg, ToolMessage) and getattr(msg, "name", None) == "intervention_required":
                    truncate_idx = idx
                    break
            if truncate_idx is not None:
                prior_messages = prior_messages[:truncate_idx]
            prior_messages.append(tool_result_message)

            new_messages_start = len(prior_messages)

            # Let the agent continue from where it left off with the tool result
            domain_agent = make_lead_agent(agent_config_override)
            result = await domain_agent.ainvoke(
                {"messages": prior_messages},
                config=agent_config_override,
            )
            messages = result.get("messages") or []
        else:
            # --- Normal / continue_after_dependency / continue_after_intervention / continue_after_clarification ---
            # All share the same invocation pattern: restore prior messages + new context.
            prior_messages = _deserialize_agent_messages(task.get("agent_messages"))
            if prior_messages:
                # Strip intervention_required ToolMessage and any trailing messages
                for _idx, _msg in enumerate(prior_messages):
                    if isinstance(_msg, ToolMessage) and getattr(_msg, "name", None) == "intervention_required":
                        prior_messages = prior_messages[:_idx]
                        break
                input_messages = prior_messages + [HumanMessage(content=context)]
                new_messages_start = len(prior_messages)
                logger.info(
                    "[Executor] Resuming with %d prior messages + new context for task '%s'.",
                    len(prior_messages),
                    task["task_id"],
                )
            else:
                input_messages = [HumanMessage(content=context)]
                new_messages_start = 0

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

        # ---------------------------------------------------------------
        # Outcome normalization — only current-round messages are inspected
        # ---------------------------------------------------------------
        # Clamp new_messages_start: if the agent returned fewer messages than
        # the prior history (e.g. the agent framework returns only new messages
        # rather than the full conversation), treat all returned messages as
        # current-round output.
        if new_messages_start >= len(messages):
            new_messages_start = 0

        outcome, used_fallback = normalize_agent_outcome(
            task=task,
            messages=messages,
            new_messages_start=new_messages_start,
        )

        logger.info(
            "[Executor] Outcome normalized task_id=%s continuation_mode=%s "
            "new_messages_start=%s outcome_kind=%s used_fallback=%s",
            task["task_id"],
            continuation_mode,
            new_messages_start,
            outcome["kind"],
            used_fallback,
        )
        record_decision(
            "outcome_classification",
            run_id=task_run_id,
            task_id=task["task_id"],
            agent_name=agent_name,
            inputs={
                "message_count": len(messages),
                "last_message_type": messages[-1].__class__.__name__ if messages else None,
                "last_message_name": getattr(messages[-1], "name", None) if messages else None,
            },
            output={"outcome_kind": outcome["kind"], "used_fallback": used_fallback},
        )

        # ---------------------------------------------------------------
        # Branch on outcome.kind
        # ---------------------------------------------------------------
        outcome_kind = outcome["kind"]

        # --- request_intervention ---
        if outcome_kind == "request_intervention":
            intervention_request = outcome.get("intervention_request") or {}
            if not intervention_request.get("request_id"):
                # Legacy fallback: parse from ToolMessage directly
                terminal = _find_last_terminal_tool_signal(messages)
                if terminal and terminal[1].name == "intervention_required":
                    intervention_request = _parse_intervention_required_message(terminal[1]) or {}
            if not intervention_request.get("request_id"):
                raise RuntimeError("intervention_required returned invalid structured payload.")

            _log_executor_decision(
                task, agent_name, messages, "intervention_required",
                outcome_kind=outcome_kind,
                used_fallback=used_fallback,
                intervention_request_id=intervention_request.get("request_id"),
                intervention_fingerprint=intervention_request.get("fingerprint"),
            )
            record_decision(
                "intervention_trigger",
                run_id=task_run_id,
                task_id=task["task_id"],
                agent_name=agent_name,
                inputs={"tool_name": intervention_request.get("tool_name", ""), "tool_args_keys": list((intervention_request.get("context") or {}).keys())},
                output={"request_id": intervention_request.get("request_id"), "risk_level": intervention_request.get("risk_level", "")},
            )
            serialized_messages = _serialize_agent_messages(messages)
            intercepted_tool = _extract_intercepted_tool_call(messages)
            # Extract idempotency_key from intervention_request.context (set by middleware)
            ir_context = intervention_request.get("context") or {}
            idempotency_key = ir_context.get("idempotency_key")
            interrupt_fingerprint = intervention_request.get("fingerprint")

            # Build pending_tool_call from outcome or legacy extraction
            pending_tool = None
            if outcome.get("pending_tool_call"):
                ptc = outcome["pending_tool_call"]
                pending_tool = _build_pending_tool_call(
                    ptc["tool_name"],
                    ptc["tool_args"],
                    tool_call_id=ptc.get("tool_call_id"),
                    idempotency_key=idempotency_key or ptc.get("idempotency_key"),
                    source_agent=agent_name,
                    source_task_id=task["task_id"],
                    interrupt_fingerprint=interrupt_fingerprint,
                )
            elif intercepted_tool:
                pending_tool = _build_pending_tool_call(
                    intercepted_tool["tool_name"],
                    intercepted_tool["tool_args"],
                    tool_call_id=intercepted_tool.get("tool_call_id"),
                    idempotency_key=idempotency_key,
                    source_agent=agent_name,
                    source_task_id=task["task_id"],
                    interrupt_fingerprint=interrupt_fingerprint,
                )

            suppressed_signals = outcome.get("suppressed_signals") or []
            if any(signal.startswith("request_help") for signal in suppressed_signals):
                _log_interrupt_event(
                    "interrupt_followup_suppressed",
                    run_id=task.get("run_id"),
                    task_id=task["task_id"],
                    agent_name=agent_name,
                    source_signal=outcome.get("selected_signal"),
                    suppressed_signals=suppressed_signals,
                    intervention_request_id=intervention_request.get("request_id"),
                    fingerprint=interrupt_fingerprint,
                )

            _log_interrupt_event(
                "interrupt_selected_as_authoritative",
                run_id=task.get("run_id"),
                task_id=task["task_id"],
                agent_name=agent_name,
                source_signal=outcome.get("selected_signal") or intervention_request.get("source_signal") or "intervention_required",
                intervention_request_id=intervention_request.get("request_id"),
                fingerprint=interrupt_fingerprint,
                semantic_key=intervention_request.get("semantic_key"),
                suppressed_signals=suppressed_signals,
            )

            intervention_task: TaskStatus = {
                **task,
                "status": "WAITING_INTERVENTION",
                "status_detail": "@waiting_intervention",
                "intervention_request": intervention_request,
                "intervention_status": "pending",
                "intervention_fingerprint": intervention_request.get("fingerprint"),
                "intervention_resolution": None,
                "clarification_prompt": None,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": serialized_messages,
                "intercepted_tool_call": intercepted_tool,  # keep for compatibility
                # Phase 2: structured continuation state
                "continuation_mode": "resume_tool_call",
                "pending_interrupt": _build_pending_interrupt(
                    interrupt_type="intervention",
                    request_id=intervention_request.get("request_id"),
                    fingerprint=interrupt_fingerprint,
                    interrupt_kind=_interrupt_kind_from_request(intervention_request),
                    semantic_key=intervention_request.get("semantic_key"),
                    source_signal=intervention_request.get("source_signal") or "intervention_required",
                    source_agent=agent_name,
                ),
                "pending_tool_call": pending_tool,
                "updated_at": _utc_now_iso(),
            }
            _log_interrupt_event(
                "interrupt_created",
                run_id=task.get("run_id"),
                task_id=task["task_id"],
                agent_name=agent_name,
                source_signal=intervention_request.get("source_signal") or "intervention_required",
                intervention_request_id=intervention_request.get("request_id"),
                fingerprint=interrupt_fingerprint,
                semantic_key=intervention_request.get("semantic_key"),
                interrupt_kind=_interrupt_kind_from_request(intervention_request),
                tool_name=(pending_tool or {}).get("tool_name"),
            )
            _emit_task_event(
                writer, "task_waiting_intervention", intervention_task, agent_name,
                status_detail="@waiting_intervention",
                intervention_request=intervention_request,
            )
            return _with_intervention_cache({
                "task_pool": [intervention_task],
                "execution_state": "INTERRUPTED",
            })

        # --- request_dependency ---
        if outcome_kind == "request_dependency":
            help_payload = outcome.get("help_request") or {}
            # Parse structured help request from the raw payload
            terminal = _find_last_terminal_tool_signal(messages)
            if terminal and terminal[1].name == "request_help":
                help_request = _parse_request_help_message(terminal[1])
            else:
                help_request = None
            if help_request is None:
                # Build minimal from outcome payload
                help_request = {
                    "problem": help_payload.get("problem", ""),
                    "required_capability": help_payload.get("required_capability", ""),
                    "reason": help_payload.get("reason", ""),
                    "expected_output": help_payload.get("expected_output", ""),
                }
                if not (help_request["problem"] and help_request["reason"]):
                    raise RuntimeError("request_help returned invalid structured payload.")

            _log_executor_decision(
                task, agent_name, messages, "request_help",
                outcome_kind=outcome_kind,
                used_fallback=used_fallback,
                help_request_reason=help_request.get("reason"),
                help_request_expected_output=help_request.get("expected_output"),
            )

            next_help_depth = int(task.get("help_depth") or 0) + 1
            serialized_messages = _serialize_agent_messages(messages)

            # ── User-owned help request normalization ──
            # Detect user-owned blocking (user_clarification, user_confirmation,
            # user_multi_select, or presence of clarification_options) and write
            # WAITING_INTERVENTION directly instead of WAITING_DEPENDENCY.
            # This prevents the mixed-state bug where status=WAITING_DEPENDENCY
            # but intervention payload is active.
            from src.agents.intervention.help_request_builder import (
                build_help_request_intervention,
                normalize_clarification_options,
                resolve_user_interaction_kind,
                should_interrupt_for_user_clarification,
            )

            if should_interrupt_for_user_clarification(help_request):
                options = normalize_clarification_options(help_request.get("clarification_options"))
                strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
                question = str(help_request.get("clarification_question") or "").strip()
                if question or options or strategy in {"user_confirmation", "user_multi_select"}:
                    semantic_fp = generate_clarification_semantic_fingerprint(agent_name, question, options)
                    cached = intervention_cache.get(semantic_fp)
                    if cached:
                        if is_intervention_cache_valid(cached, require_resume_behavior=False):
                            logger.info(
                                "[Executor] [Cache HIT] clarification semantic_fp=%s reuse_count=%s/%s",
                                semantic_fp,
                                cached.get("reuse_count", 0),
                                cached.get("max_reuse", DEFAULT_CLARIFICATION_MAX_REUSE),
                            )
                            return _auto_resume_from_clarification_cache(
                                task,
                                cached=cached,
                                semantic_fp=semantic_fp,
                                intervention_cache=intervention_cache,
                                serialized_messages=serialized_messages,
                                next_help_depth=next_help_depth,
                                agent_name=agent_name,
                                writer=writer,
                            )
                        max_reuse = cached.get("max_reuse", DEFAULT_CLARIFICATION_MAX_REUSE)
                        reuse_count = cached.get("reuse_count", 0)
                        if max_reuse != -1 and reuse_count >= max_reuse:
                            logger.info(
                                "[Executor] [Cache EXPIRED] clarification semantic_fp=%s reuse_count=%s reached max_reuse=%s",
                                semantic_fp,
                                reuse_count,
                                max_reuse,
                            )

                    intervention_request = build_help_request_intervention(
                        task, help_request, agent_name=agent_name,
                    )
                    interaction_kind = resolve_user_interaction_kind(help_request, options)
                    interrupt_kind = (
                        "confirmation" if interaction_kind == "confirm"
                        else "selection" if interaction_kind in {"single_select", "multi_select", "select"}
                        else "clarification"
                    )
                    user_intervention_task: TaskStatus = {
                        **task,
                        "status": "WAITING_INTERVENTION",
                        "requested_by_agent": agent_name,
                        "request_help": help_request,
                        "blocked_reason": None,
                        "help_depth": next_help_depth,
                        "clarification_prompt": None,
                        "clarification_request": None,
                        "intervention_request": intervention_request,
                        "intervention_status": "pending",
                        "intervention_fingerprint": intervention_request["fingerprint"],
                        "intervention_resolution": None,
                        "status_detail": "@waiting_intervention",
                        "agent_messages": serialized_messages,
                        "continuation_mode": "continue_after_intervention",
                        "pending_interrupt": _build_pending_interrupt(
                            interrupt_type="intervention",
                            request_id=intervention_request["request_id"],
                            fingerprint=intervention_request["fingerprint"],
                            interrupt_kind=interrupt_kind,
                            semantic_key=intervention_request.get("semantic_key"),
                            source_signal=intervention_request.get("source_signal") or "request_help",
                            source_agent=agent_name,
                            prompt=question or None,
                            options=options or None,
                        ),
                        "pending_tool_call": None,
                        "updated_at": _utc_now_iso(),
                    }
                    _log_interrupt_event(
                        "interrupt_selected_as_authoritative",
                        run_id=task.get("run_id"),
                        task_id=task["task_id"],
                        agent_name=agent_name,
                        source_signal=outcome.get("selected_signal") or "request_help_user",
                        intervention_request_id=intervention_request["request_id"],
                        fingerprint=intervention_request["fingerprint"],
                        semantic_key=intervention_request.get("semantic_key"),
                        suppressed_signals=outcome.get("suppressed_signals") or [],
                    )
                    _log_interrupt_event(
                        "interrupt_created",
                        run_id=task.get("run_id"),
                        task_id=task["task_id"],
                        agent_name=agent_name,
                        source_signal=intervention_request.get("source_signal") or "request_help",
                        intervention_request_id=intervention_request["request_id"],
                        fingerprint=intervention_request["fingerprint"],
                        semantic_key=intervention_request.get("semantic_key"),
                        interrupt_kind=interrupt_kind,
                    )
                    _emit_task_event(
                        writer, "task_waiting_intervention", user_intervention_task, agent_name,
                        status_detail="@waiting_intervention",
                        intervention_request=intervention_request,
                        intervention_status="pending",
                        intervention_fingerprint=intervention_request["fingerprint"],
                    )
                    _emit_task_event(
                        writer, "task_help_requested", user_intervention_task, agent_name,
                        request_help=help_request,
                        requested_by_agent=agent_name,
                        status_detail="@waiting_intervention",
                    )
                    return _with_intervention_cache({
                        "task_pool": [user_intervention_task],
                        "execution_state": "INTERRUPTED",
                    })

            # ── True system dependency ──
            # Only system-owned blocking reaches WAITING_DEPENDENCY.
            waiting_task: TaskStatus = {
                **task,
                "status": "WAITING_DEPENDENCY",
                "requested_by_agent": agent_name,
                "request_help": help_request,
                "blocked_reason": help_request["reason"],
                "help_depth": next_help_depth,
                "clarification_prompt": None,
                "clarification_request": None,
                "status_detail": "@waiting_dependency",
                "agent_messages": serialized_messages,
                # Phase 2: structured continuation state
                "continuation_mode": "continue_after_dependency",
                "pending_interrupt": _build_pending_interrupt(
                    interrupt_type="dependency",
                    source_signal=outcome.get("selected_signal") or "request_help_system",
                    source_agent=agent_name,
                ),
                "pending_tool_call": None,
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(
                writer, "task_waiting_dependency", waiting_task, agent_name,
                blocked_reason=help_request["reason"],
                request_help=help_request,
                requested_by_agent=agent_name,
                status_detail="@waiting_dependency",
            )
            _emit_task_event(
                writer, "task_help_requested", waiting_task, agent_name,
                request_help=help_request,
                requested_by_agent=agent_name,
                status_detail="@waiting_dependency",
            )
            return _with_intervention_cache({
                "task_pool": [waiting_task],
                "execution_state": "EXECUTING_DONE",
            })

        # --- request_clarification ---
        if outcome_kind == "request_clarification":
            clarification_prompt = outcome.get("prompt", "")
            classification = "implicit_clarification" if used_fallback else "ask_clarification"
            _log_executor_decision(
                task, agent_name, messages, classification,
                outcome_kind=outcome_kind,
                used_fallback=used_fallback,
                clarification_prompt=clarification_prompt[:300],
            )
            serialized_messages = _serialize_agent_messages(messages)
            interrupted_task: TaskStatus = {
                **task,
                "status": "RUNNING",
                "status_detail": "@waiting_clarification",
                "clarification_prompt": clarification_prompt,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": serialized_messages,
                # Phase 2: structured continuation state
                "continuation_mode": "continue_after_clarification",
                "pending_interrupt": _build_pending_interrupt(
                    interrupt_type="clarification",
                    interrupt_kind="clarification",
                    source_signal="ask_clarification",
                    source_agent=agent_name,
                    prompt=clarification_prompt,
                ),
                "pending_tool_call": None,
                "updated_at": _utc_now_iso(),
            }
            event_message = "需要你补充信息" if not used_fallback else "Waiting for user clarification inferred from plain-text output"
            _emit_task_event(
                writer, "task_running", interrupted_task, agent_name,
                message=event_message,
                clarification_prompt=clarification_prompt,
                status="waiting_clarification",
                status_detail="@waiting_clarification",
            )
            return _with_intervention_cache({
                "task_pool": [interrupted_task],
                "execution_state": "INTERRUPTED",
                "messages": [AIMessage(content=clarification_prompt, name="ask_clarification")],
            })

        # --- fail ---
        if outcome_kind == "fail":
            error_message = outcome.get("error_message", "Unknown error")
            _log_executor_decision(
                task, agent_name, messages, "task_fail",
                outcome_kind=outcome_kind,
                used_fallback=used_fallback,
                error_message=error_message[:300],
            )
            failed_task: TaskStatus = {
                **task,
                "status": "FAILED",
                "error": error_message,
                "status_detail": "@failed",
                "clarification_prompt": None,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                **_clear_continuation_fields(),
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(writer, "task_failed", task, agent_name, error=error_message, status_detail="@failed")
            return _with_intervention_cache({
                "task_pool": [failed_task],
                "execution_state": "EXECUTING_DONE",
            })

        # --- complete (default) ---
        agent_output = outcome.get("result_text", "")
        fact_payload = outcome.get("fact_payload") or _normalize_fact_payload(agent_output)

        _log_executor_decision(
            task, agent_name, messages, "final_output",
            outcome_kind=outcome_kind,
            used_fallback=used_fallback,
            final_output_preview=agent_output[:300],
        )
        logger.info("[Executor] Task '%s' DONE. Output length=%d.", task["task_id"], len(agent_output))
        logger.info("[Executor] Agent '%s' final output: %s", agent_name, agent_output[:2000])

        # --- Phase 4: Task-level verification gate ---
        from src.verification.runtime import run_task_verification, build_verification_feedback
        from src.verification.base import VerificationVerdict, VerificationScope

        v_result = run_task_verification(
            task_id=task["task_id"],
            task_description=task["description"],
            task_result=agent_output,
            assigned_agent=agent_name,
            resolved_inputs=task.get("resolved_inputs"),
            verified_facts=state.get("verified_facts") or {},
            artifacts=state.get("artifacts") or [],
        )

        if v_result.verdict == VerificationVerdict.HARD_FAIL:
            logger.error("[Executor] Task '%s' verification HARD_FAIL: %s", task["task_id"], v_result.report.summary)
            hard_fail_task: TaskStatus = {
                **task,
                "status": "FAILED",
                "result": agent_output,
                "error": f"Verification hard_fail: {v_result.report.summary}",
                "status_detail": "@verification_hard_fail",
                "verification_status": "hard_fail",
                "verification_report": v_result.report.model_dump(),
                "clarification_prompt": None,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": None,
                "intercepted_tool_call": None,
                **_clear_continuation_fields(),
                "updated_at": _utc_now_iso(),
            }
            _emit_task_event(writer, "task_failed", task, agent_name, error=v_result.report.summary, status_detail="@verification_hard_fail")
            return _with_intervention_cache({
                "task_pool": [hard_fail_task],
                "execution_state": "ERROR",
                "final_result": f"Verification hard failure on task '{task['task_id']}': {v_result.report.summary}",
                "workflow_verification_status": "hard_fail",
                "workflow_verification_report": v_result.report.model_dump(),
            })

        if v_result.verdict == VerificationVerdict.NEEDS_REPLAN:
            logger.warning("[Executor] Task '%s' verification NEEDS_REPLAN: %s", task["task_id"], v_result.report.summary)
            replan_task: TaskStatus = {
                **task,
                "status": "FAILED",
                "result": agent_output,
                "error": f"Verification needs_replan: {v_result.report.summary}",
                "status_detail": "@verification_needs_replan",
                "verification_status": "needs_replan",
                "verification_report": v_result.report.model_dump(),
                "clarification_prompt": None,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": None,
                "intercepted_tool_call": None,
                **_clear_continuation_fields(),
                "updated_at": _utc_now_iso(),
            }
            feedback = build_verification_feedback(v_result, VerificationScope.TASK_RESULT, task["task_id"])
            _emit_task_event(writer, "task_failed", task, agent_name, error=v_result.report.summary, status_detail="@verification_needs_replan")
            return _with_intervention_cache({
                "task_pool": [replan_task],
                "verification_feedback": feedback,
                "execution_state": "EXECUTING_DONE",
            })

        # --- verification passed ---
        done_task: TaskStatus = {
            **task,
            "status": "DONE",
            "result": agent_output,
            "status_detail": "@completed",
            "verification_status": "passed",
            "verification_report": v_result.report.model_dump(),
            "clarification_prompt": None,
            "clarification_request": None,
            "request_help": None,
            "blocked_reason": None,
            "agent_messages": None,
            "intercepted_tool_call": None,
            **_clear_continuation_fields(),
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_completed", task, agent_name, result=agent_output, status_detail="@completed")
        return _with_intervention_cache({
            "task_pool": [done_task],
            "verified_facts": {
                task["task_id"]: {
                    "agent": agent_name,
                    "task": task["description"],
                    "summary": agent_output,
                    "payload": fact_payload,
                    "fact_type": "task_result",
                    "source_task_id": task["task_id"],
                    "updated_at": _utc_now_iso(),
                }
            },
            "execution_state": "EXECUTING_DONE",
        })
    except Exception as e:
        logger.error("[Executor] Task '%s' FAILED: %s", task["task_id"], e, exc_info=True)
        failed_task: TaskStatus = {
            **task,
            "status": "FAILED",
            "error": str(e),
            "status_detail": "@failed",
            "clarification_prompt": None,
            "clarification_request": None,
            "request_help": None,
            "blocked_reason": None,
            **_clear_continuation_fields(),
            "updated_at": _utc_now_iso(),
        }
        _emit_task_event(writer, "task_failed", task, agent_name, error=str(e), status_detail="@failed")
        return _with_intervention_cache({
            "task_pool": [failed_task],
            "execution_state": "EXECUTING_DONE",
        })
