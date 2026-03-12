"""Semantic Router node for multi-agent task assignment."""

import json
import logging
import re
from datetime import datetime, timezone
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.thread_state import HelpRequestPayload, TaskStatus, ThreadState
from src.config.agents_config import list_domain_agents
from src.models import create_chat_model

logger = logging.getLogger(__name__)

MAX_ROUTE_COUNT = 12
MAX_HELP_DEPTH = 2
MAX_RESUME_COUNT = 2
MAX_HELPER_RETRY_COUNT = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_run_id(state: ThreadState, task: TaskStatus) -> str:
    return state.get("run_id") or task.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"


def _resolve_model(config: RunnableConfig) -> str | None:
    return config.get("configurable", {}).get("model_name") or config.get("configurable", {}).get("model")


def _build_agent_profiles(agents) -> str:
    return "\n".join(f"- {a.name}: {a.description}" for a in agents)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


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


ROUTER_SYSTEM_PROMPT = """你是一个精准的任务路由调度器。请严格按要求输出。"""

ROUTER_USER_PROMPT = """可用 Domain Agent：
{agent_profiles}

待处理子任务：
<task>{task_description}</task>

请返回最合适的 Agent ID，并且仅输出 <route>...</route>。
如果都不匹配，输出 <route>SYSTEM_FALLBACK</route>。"""


async def _llm_route(task_description: str, agent_profiles: str, valid_agent_names: list[str], config: RunnableConfig) -> str:
    llm = create_chat_model(name=_resolve_model(config), thinking_enabled=False)
    messages = [
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=ROUTER_USER_PROMPT.format(agent_profiles=agent_profiles, task_description=task_description)),
    ]
    try:
        response = await llm.ainvoke(messages)
        output = _content_to_text(response.content)

        match = re.search(r"<route>(.*?)</route>", output, re.DOTALL | re.IGNORECASE)
        if match:
            target = match.group(1).strip()
            if target in valid_agent_names or target == "SYSTEM_FALLBACK":
                return target
            logger.warning("[Router] LLM returned unknown agent '%s', falling back.", target)
            return "SYSTEM_FALLBACK"

        logger.warning("[Router] No <route> tag found. Raw output: %r", output[:200])
        escaped = [re.escape(n) for n in valid_agent_names]
        fallback_pattern = "|".join(escaped + ["SYSTEM_FALLBACK"])
        fb_match = re.search(f"({fallback_pattern})", output)
        if fb_match:
            return fb_match.group(1).strip()

        return "SYSTEM_FALLBACK"
    except Exception as e:
        logger.error("[Router] LLM routing failed: %s", e)
        return "SYSTEM_FALLBACK"


def _get_event_writer():
    try:
        return get_stream_writer()
    except Exception:
        return lambda _event: None


def _emit_task_event(writer, event_type: str, task: TaskStatus, agent_name: str, **extra: Any) -> None:
    payload = {
        "type": event_type,
        "source": "multi_agent",
        "run_id": task.get("run_id"),
        "task_id": task["task_id"],
        "agent_name": agent_name,
        "description": task["description"],
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    writer(payload)


def _build_helper_description(help_request: HelpRequestPayload) -> str:
    return (
        f"Blocked problem: {help_request['problem']}\n"
        f"Required capability: {help_request['required_capability']}\n"
        f"Reason: {help_request['reason']}\n"
        f"Expected output: {help_request['expected_output']}"
    )


def _append_candidate_hints(task_description: str, hinted_agents: list[str]) -> str:
    if not hinted_agents:
        return task_description
    hinted = ", ".join(hinted_agents)
    return f"{task_description}\nPreferred helper candidates (hint only): {hinted}"


def _get_helper_candidates(help_request: HelpRequestPayload, requester: str | None) -> tuple[list, list[str], list[str]]:
    domain_agents = list_domain_agents()
    candidate_names = [agent.name for agent in domain_agents if agent.name != requester]
    hinted = [name for name in (help_request.get("candidate_agents") or []) if name in candidate_names]
    filtered_agents = [agent for agent in domain_agents if agent.name in candidate_names]
    return filtered_agents, candidate_names, hinted


def _pick_direct_helper_candidate(candidate_names: list[str], hinted: list[str]) -> str | None:
    if len(hinted) == 1:
        return hinted[0]
    if len(candidate_names) == 1:
        return candidate_names[0]
    return None


def _can_retry_helper(parent_task: TaskStatus) -> bool:
    return int(parent_task.get("helper_retry_count") or 0) < MAX_HELPER_RETRY_COUNT


def _route_to_helper(
    parent_task: TaskStatus,
    state: ThreadState,
    route_count: int,
    assigned: str,
    *,
    status_detail: str | None = None,
    helper_retry_count: int | None = None,
) -> dict:
    run_id = _resolve_run_id(state, parent_task)
    help_depth = int(parent_task.get("help_depth") or 0)
    helper_task_id = str(uuid.uuid4())[:8]
    helper_task: TaskStatus = {
        "task_id": helper_task_id,
        "description": _build_helper_description(parent_task["request_help"]),
        "run_id": run_id,
        "parent_task_id": parent_task["task_id"],
        "assigned_agent": assigned,
        "status": "RUNNING",
        "status_detail": f"Assigned to {assigned}",
        "requested_by_agent": parent_task.get("requested_by_agent"),
        "help_depth": help_depth,
        "updated_at": _utc_now_iso(),
    }
    updated_parent: TaskStatus = {
        **parent_task,
        "depends_on_task_ids": [helper_task_id],
        "status_detail": status_detail or f"Waiting for helper {assigned}",
        "updated_at": _utc_now_iso(),
    }
    if helper_retry_count is not None:
        updated_parent["helper_retry_count"] = helper_retry_count
    logger.info(
        "[Router] Created helper task helper_task=%s assigned_agent=%s parent_task=%s helper_retry_count=%s",
        helper_task_id,
        assigned,
        parent_task["task_id"],
        updated_parent.get("helper_retry_count"),
    )
    return {
        "task_pool": [updated_parent, helper_task],
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
    }


def _normalize_clarification_options(options: Any) -> list[str]:
    if not isinstance(options, list):
        return []
    return [str(option).strip() for option in options if str(option).strip()]


def _should_interrupt_for_user_clarification(help_request: HelpRequestPayload) -> bool:
    strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
    return strategy == "user_clarification" or bool(str(help_request.get("clarification_question") or "").strip())


def _build_user_clarification_prompt(parent_task: TaskStatus, help_request: HelpRequestPayload) -> str:
    question = str(help_request.get("clarification_question") or "").strip()
    context = str(help_request.get("clarification_context") or "").strip()
    options = _normalize_clarification_options(help_request.get("clarification_options"))

    if not question:
        return _build_clarification_prompt(parent_task, help_request)

    lines: list[str] = []
    if context:
        lines.append(context)
        lines.append("")
    lines.append(question)
    if options:
        lines.append("")
        for idx, option in enumerate(options, start=1):
            lines.append(f"{idx}. {option}")
    return "\n".join(lines)


def _build_clarification_prompt(parent_task: TaskStatus, help_request: HelpRequestPayload) -> str:
    expected = help_request.get("expected_output", "").strip()
    problem = help_request.get("problem", "").strip()
    capability = help_request.get("required_capability", "").strip()
    details = expected or capability or problem or parent_task["description"]
    return f"我当前缺少继续完成该任务所需的信息或决策，请补充：{details}"


def _build_dependency_failure_prompt(parent_task: TaskStatus, dependency_tasks: list[TaskStatus]) -> str:
    expected = ""
    help_request = parent_task.get("request_help")
    if help_request:
        expected = str(help_request.get("expected_output", "")).strip()

    failures: list[str] = []
    for dependency_task in dependency_tasks:
        if dependency_task.get("status") != "FAILED":
            continue
        helper_name = dependency_task.get("assigned_agent") or dependency_task["task_id"]
        reason = (
            dependency_task.get("error")
            or dependency_task.get("status_detail")
            or "依赖任务执行失败"
        )
        failures.append(f"{helper_name}: {reason}")

    failure_summary = "；".join(failures) if failures else "依赖任务执行失败"
    if expected:
        return f"依赖任务未能完成（{failure_summary}）。请补充所需信息或指定下一步处理方式：{expected}"
    return f"依赖任务未能完成（{failure_summary}）。请补充所需信息或指定下一步处理方式。"


def _summarize_dependency_failures(dependency_tasks: list[TaskStatus]) -> str:
    failures: list[str] = []
    for dependency_task in dependency_tasks:
        if dependency_task.get("status") != "FAILED":
            continue
        helper_name = dependency_task.get("assigned_agent") or dependency_task["task_id"]
        reason = (
            dependency_task.get("error")
            or dependency_task.get("status_detail")
            or "依赖任务执行失败"
        )
        failures.append(f"{helper_name}: {reason}")
    return "；".join(failures) if failures else "依赖任务执行失败"


def _interrupt_for_clarification(
    parent_task: TaskStatus,
    prompt: str,
    route_count: int,
    writer,
    *,
    agent_name: str,
    status_detail: str,
) -> dict:
    resumed_task: TaskStatus = {
        **parent_task,
        "status": "RUNNING",
        "request_help": None,
        "depends_on_task_ids": [],
        "blocked_reason": None,
        "clarification_prompt": prompt,
        "status_detail": status_detail,
        "updated_at": _utc_now_iso(),
    }
    _emit_task_event(
        writer,
        "task_running",
        resumed_task,
        agent_name,
        status="waiting_clarification",
        clarification_prompt=prompt,
        status_detail=status_detail,
    )
    return {
        "task_pool": [resumed_task],
        "execution_state": "INTERRUPTED",
        "route_count": route_count,
        "messages": [AIMessage(content=prompt, name="ask_clarification")],
    }


def _resume_parent_from_helper(
    parent: TaskStatus,
    dependency_ids: list[str],
    task_pool: list[TaskStatus],
    verified_facts: dict[str, Any],
) -> TaskStatus | None:
    dependency_tasks = [task for task in task_pool if task["task_id"] in dependency_ids]
    if not dependency_tasks or any(task["status"] != "DONE" for task in dependency_tasks):
        return None

    resolved_inputs: dict[str, Any] = dict(parent.get("resolved_inputs") or {})
    for dependency_task in dependency_tasks:
        fact_entry = verified_facts.get(dependency_task["task_id"])
        result_payload: Any = None
        if isinstance(fact_entry, dict):
            result_payload = fact_entry.get("payload")
            if result_payload is None:
                result_payload = fact_entry.get("summary")

        if result_payload is None:
            result_payload = dependency_task.get("result")

        if isinstance(result_payload, str):
            try:
                result_payload = json.loads(result_payload)
            except Exception:
                result_payload = {"text": result_payload}
        resolved_inputs[dependency_task["task_id"]] = result_payload

    return {
        **parent,
        "status": "RUNNING",
        "request_help": None,
        "depends_on_task_ids": [],
        "blocked_reason": None,
        "clarification_prompt": None,
        "status_detail": "Dependency resolved; resuming execution",
        "resolved_inputs": resolved_inputs,
        "resume_count": int(parent.get("resume_count") or 0) + 1,
        "updated_at": _utc_now_iso(),
    }


async def _route_help_request(parent_task: TaskStatus, state: ThreadState, config: RunnableConfig, route_count: int) -> dict:
    help_request = parent_task.get("request_help")
    writer = _get_event_writer()
    if not help_request:
        return {"execution_state": "PLANNING_NEEDED", "route_count": route_count}

    logger.info(
        "[Router] route_help_request parent_task=%s requester=%s payload=%s",
        parent_task["task_id"],
        parent_task.get("requested_by_agent"),
        json.dumps(help_request, ensure_ascii=False)[:2000],
    )

    if _should_interrupt_for_user_clarification(help_request):
        prompt = _build_user_clarification_prompt(parent_task, help_request)
        logger.info("[Router] Direct user clarification required for parent_task=%s", parent_task["task_id"])
        return _interrupt_for_clarification(
            parent_task,
            prompt,
            route_count,
            writer,
            agent_name=parent_task.get("assigned_agent") or "workflow-router",
            status_detail="Waiting for user clarification",
        )

    requester = parent_task.get("requested_by_agent")
    domain_agents, candidate_names, hinted = _get_helper_candidates(help_request, requester)
    direct_candidate = _pick_direct_helper_candidate(candidate_names, hinted)
    helper_retry_count = int(parent_task.get("helper_retry_count") or 0)

    help_depth = int(parent_task.get("help_depth") or 0)
    resume_count = int(parent_task.get("resume_count") or 0)
    help_loop_budget = route_count + help_depth + resume_count
    if (
        help_depth > MAX_HELP_DEPTH
        or resume_count >= MAX_RESUME_COUNT
        or help_loop_budget >= MAX_ROUTE_COUNT
    ):
        budget_reason = (
            f"helper routing budget exhausted "
            f"(help_depth={help_depth}, resume_count={resume_count}, route_count={route_count})"
        )
        if direct_candidate and _can_retry_helper(parent_task):
            next_retry_count = helper_retry_count + 1
            logger.warning(
                "[Router] %s for parent_task=%s, retrying direct helper '%s' once (retry=%d/%d)",
                budget_reason,
                parent_task["task_id"],
                direct_candidate,
                next_retry_count,
                MAX_HELPER_RETRY_COUNT,
            )
            return _route_to_helper(
                parent_task,
                state,
                route_count,
                direct_candidate,
                status_detail=f"Retrying helper {direct_candidate} after previous routing budget exhaustion",
                helper_retry_count=next_retry_count,
            )
        logger.info("[Router] %s for parent_task=%s", budget_reason, parent_task["task_id"])
        prompt = _build_clarification_prompt(parent_task, help_request)
        return _interrupt_for_clarification(
            parent_task,
            prompt,
            route_count,
            writer,
            agent_name=parent_task.get("assigned_agent") or "workflow-router",
            status_detail="Waiting for user clarification after helper routing budget was exhausted",
        )

    if not candidate_names:
        logger.info("[Router] No candidate helper agents available for parent_task=%s", parent_task["task_id"])
        prompt = _build_clarification_prompt(parent_task, help_request)
        return _interrupt_for_clarification(
            parent_task,
            prompt,
            route_count,
            writer,
            agent_name="workflow-router",
            status_detail="Waiting for user clarification because no helper agent was available",
        )

    if len(candidate_names) == 1:
        assigned = candidate_names[0]
    elif len(hinted) == 1:
        assigned = hinted[0]
        logger.info("[Router] Fast path via candidate_agents hint: parent_task=%s -> %s", parent_task["task_id"], assigned)
    else:
        logger.info(
            "[Router] Helper routing candidates parent_task=%s requester=%s candidates=%s hinted=%s",
            parent_task["task_id"],
            requester,
            candidate_names,
            hinted,
        )
        assigned = await _llm_route(
            _append_candidate_hints(_build_helper_description(help_request), hinted),
            _build_agent_profiles(domain_agents),
            candidate_names,
            config,
        )

    if assigned == "SYSTEM_FALLBACK":
        logger.info(
            "[Router] Helper routing fell back to clarification for parent_task=%s candidates=%s hinted=%s",
            parent_task["task_id"],
            candidate_names,
            hinted,
        )
        prompt = _build_clarification_prompt(parent_task, help_request)
        return _interrupt_for_clarification(
            parent_task,
            prompt,
            route_count,
            writer,
            agent_name="workflow-router",
            status_detail="Waiting for user clarification because no helper agent matched the request",
        )

    return _route_to_helper(parent_task, state, route_count, assigned)


async def router_node(state: ThreadState, config: RunnableConfig) -> dict:
    route_count = (state.get("route_count") or 0) + 1

    if route_count >= MAX_ROUTE_COUNT:
        logger.error("[Router] route_count=%d reached MAX_ROUTE_COUNT=%d, aborting.", route_count, MAX_ROUTE_COUNT)
        facts = state.get("verified_facts") or {}
        last_fact = _fact_value_to_text(facts[next(reversed(facts))]) if facts else "已尽力处理，但未能完全完成任务。"
        return {
            "execution_state": "ERROR",
            "final_result": f"[系统自动终止-防死循环] {last_fact}",
            "route_count": route_count,
        }

    task_pool: list[TaskStatus] = state.get("task_pool") or []
    running = [t for t in task_pool if t["status"] == "RUNNING"]

    if running:
        logger.info("[Router] Found RUNNING task, forwarding to executor.")
        return {
            "execution_state": "ROUTING_DONE",
            "route_count": route_count,
        }

    waiting = [t for t in task_pool if t["status"] == "WAITING_DEPENDENCY"]
    verified_facts = state.get("verified_facts") or {}
    writer = _get_event_writer()
    for waiting_task in waiting:
        dependency_ids = waiting_task.get("depends_on_task_ids") or []
        if dependency_ids:
            logger.info("[Router] Waiting task '%s' depends_on=%s", waiting_task["task_id"], dependency_ids)
            dependency_tasks = [task for task in task_pool if task["task_id"] in dependency_ids]
            if dependency_tasks and any(task["status"] == "FAILED" for task in dependency_tasks):
                failure_summary = _summarize_dependency_failures(dependency_tasks)
                help_request = waiting_task.get("request_help")
                if help_request:
                    requester = waiting_task.get("requested_by_agent")
                    _, candidate_names, hinted = _get_helper_candidates(help_request, requester)
                    direct_candidate = _pick_direct_helper_candidate(candidate_names, hinted)
                    helper_retry_count = int(waiting_task.get("helper_retry_count") or 0)
                    if direct_candidate and _can_retry_helper(waiting_task):
                        next_retry_count = helper_retry_count + 1
                        logger.warning(
                            "[Router] Dependency helper failed for parent_task=%s (%s), retrying direct helper '%s' once (retry=%d/%d)",
                            waiting_task["task_id"],
                            failure_summary,
                            direct_candidate,
                            next_retry_count,
                            MAX_HELPER_RETRY_COUNT,
                        )
                        return _route_to_helper(
                            waiting_task,
                            state,
                            route_count,
                            direct_candidate,
                            status_detail=f"Retrying helper {direct_candidate} after dependency failure",
                            helper_retry_count=next_retry_count,
                        )
                logger.info(
                    "[Router] Dependency helper failed for parent_task=%s and will be escalated to user clarification: %s",
                    waiting_task["task_id"],
                    failure_summary,
                )
                prompt = _build_dependency_failure_prompt(waiting_task, dependency_tasks)
                return _interrupt_for_clarification(
                    waiting_task,
                    prompt,
                    route_count,
                    writer,
                    agent_name=waiting_task.get("assigned_agent") or "workflow-router",
                    status_detail=f"Dependency resolution failed; waiting for user clarification ({failure_summary})",
                )
            resumed_task = _resume_parent_from_helper(waiting_task, dependency_ids, task_pool, verified_facts)
            if resumed_task is not None:
                logger.info(
                    "[Router] Resuming parent task '%s' with resolved_input_keys=%s",
                    waiting_task["task_id"],
                    list((resumed_task.get("resolved_inputs") or {}).keys()),
                )
                _emit_task_event(
                    writer,
                    "task_resumed",
                    resumed_task,
                    resumed_task.get("assigned_agent") or "workflow-router",
                    status="in_progress",
                    status_detail="Dependency resolved; task resumed",
                    resolved_inputs=resumed_task.get("resolved_inputs"),
                    resume_count=resumed_task.get("resume_count"),
                )
                return {
                    "task_pool": [resumed_task],
                    "execution_state": "ROUTING_DONE",
                    "route_count": route_count,
                }

    pending = [t for t in task_pool if t["status"] == "PENDING"]
    if not pending and waiting:
        for waiting_task in waiting:
            if waiting_task.get("request_help") and not (waiting_task.get("depends_on_task_ids") or []):
                logger.info("[Router] Resolving help request for waiting task '%s'.", waiting_task["task_id"])
                return await _route_help_request(waiting_task, state, config, route_count)

    if not pending:
        logger.info("[Router] No pending tasks, signaling planner.")
        return {"execution_state": "PLANNING_NEEDED", "route_count": route_count}

    task = pending[0]
    logger.info(
        "[Router] Pending task selected task_id=%s assigned_agent=%s description=%r",
        task["task_id"],
        task.get("assigned_agent"),
        task["description"][:300],
    )
    run_id = _resolve_run_id(state, task)
    domain_agents = list_domain_agents()
    valid_names = [a.name for a in domain_agents]

    if task.get("assigned_agent") and task["assigned_agent"] in valid_names:
        assigned = task["assigned_agent"]
        logger.info("[Router] Fast path: task '%s' -> %s", task["task_id"], assigned)
    else:
        agent_profiles = _build_agent_profiles(domain_agents)
        assigned = await _llm_route(task["description"], agent_profiles, valid_names, config)
        logger.info("[Router] LLM route: task '%s' -> %s", task["task_id"], assigned)

    updated_task: TaskStatus = {
        **task,
        "run_id": run_id,
        "status": "RUNNING",
        "assigned_agent": assigned,
        "status_detail": f"Assigned to {assigned}",
        "clarification_prompt": None,
        "updated_at": _utc_now_iso(),
    }
    return {
        "task_pool": [updated_task],
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
    }
