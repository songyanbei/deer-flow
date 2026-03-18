"""Semantic Router node for multi-agent task assignment."""

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.thread_state import (
    ClarificationRequest,
    HelpRequestPayload,
    InterventionRequest,
    InterventionResolution,
    TaskStatus,
    ThreadState,
    WorkflowStage,
)
from src.agents.workflow_resume import is_intervention_resolution_message
from src.config.agents_config import list_domain_agents
from src.models import create_chat_model

logger = logging.getLogger(__name__)

MAX_ROUTE_COUNT = 12
MAX_HELP_DEPTH = 2
MAX_RESUME_COUNT = 2
MAX_HELPER_RETRY_COUNT = 1


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    writer,
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


def _pick_first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
        elif value is not None:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def _build_executing_detail(task: TaskStatus) -> str | None:
    return _pick_first_non_empty(
        task.get("status_detail"),
        task.get("blocked_reason"),
        task.get("description"),
    )


def _build_helper_description(help_request: HelpRequestPayload) -> str:
    """Build a user-friendly, short description for the helper task card.

    The full technical detail (problem, reason, expected_output) is preserved
    in the task's ``request_help`` payload and injected into the executor
    context via ``_build_context``, so it is still available to the agent.
    """
    problem = (help_request.get("problem") or "").strip()
    if problem:
        return problem
    return help_request.get("required_capability", "") or "协助处理依赖任务"


def _build_helper_context(help_request: HelpRequestPayload) -> str:
    """Build the full technical context sent to the helper agent as prompt."""
    lines: list[str] = []
    for key in ("problem", "required_capability", "reason", "expected_output"):
        value = (help_request.get(key) or "").strip()  # type: ignore[arg-type]
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


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
        "helper_context": _build_helper_context(parent_task["request_help"]),
        "run_id": run_id,
        "parent_task_id": parent_task["task_id"],
        "assigned_agent": assigned,
        "status": "RUNNING",
        "status_detail": f"@assigned:{assigned}",
        "requested_by_agent": parent_task.get("requested_by_agent"),
        "help_depth": help_depth,
        "updated_at": _utc_now_iso(),
    }
    updated_parent: TaskStatus = {
        **parent_task,
        "depends_on_task_ids": [helper_task_id],
        "status_detail": status_detail or f"@waiting_helper:{assigned}",
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


def _build_intervention_options(options: list[str]) -> list[dict[str, str]]:
    return [{"label": option, "value": option} for option in options]


def _clean_question_segment(segment: str) -> str:
    fullwidth_comma = chr(0xFF0C)
    fullwidth_exclamation = chr(0xFF01)
    fullwidth_colon = chr(0xFF1A)
    text = str(segment or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"^\s*(?:\d+[\.、\)]\s*|[-—–•·]+\s*)",
        "",
        text,
    ).strip()
    text = re.sub(
        rf"^(?:你好[!{fullwidth_exclamation},{fullwidth_comma}\s]*|您好[!{fullwidth_exclamation},{fullwidth_comma}\s]*|麻烦您[,{fullwidth_comma}\s]*)",
        "",
        text,
    ).strip()
    if fullwidth_colon in text:
        prefix, suffix = text.split(fullwidth_colon, 1)
        if suffix.strip() and any(
            token in prefix
            for token in (
                "请",
                "提供",
                "告诉",
                "信息",
            )
        ):
            text = suffix.strip()
    return text.strip()


def _extract_clarification_questions(question: str) -> list[str]:
    fullwidth_qmark = chr(0xFF1F)
    normalized = str(question or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []

    raw_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    numbered_lines = [
        line for line in raw_lines if re.match(r"^\d+[\.、\)]\s*", line)
    ]
    if len(numbered_lines) >= 2:
        return [
            cleaned
            for cleaned in (_clean_question_segment(line) for line in numbered_lines)
            if cleaned
        ]

    line_questions = [
        _clean_question_segment(line)
        for line in raw_lines
        if fullwidth_qmark in line or "?" in line
    ]
    line_questions = [line for line in line_questions if line]
    if len(line_questions) >= 2:
        return line_questions

    sentence_candidates: list[str] = []
    buffer = ""
    for char in normalized:
        buffer += char
        if char in {fullwidth_qmark, "?"}:
            cleaned = _clean_question_segment(buffer)
            if cleaned:
                sentence_candidates.append(cleaned)
            buffer = ""
    if buffer.strip():
        cleaned = _clean_question_segment(buffer)
        if cleaned:
            sentence_candidates.append(cleaned)
    question_candidates = [
        part
        for part in sentence_candidates
        if part and (fullwidth_qmark in part or "?" in part)
    ]
    if len(question_candidates) >= 2:
        return question_candidates

    cleaned = _clean_question_segment(normalized)
    return [cleaned] if cleaned else []


def _resolve_user_interaction_kind(help_request: HelpRequestPayload, options: list[str]) -> str:
    strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
    if strategy == "user_confirmation":
        return "confirm"
    if strategy == "user_multi_select":
        return "multi_select"
    if options:
        return "single_select"
    return "input"


def _infer_question_kind(question: str, *, index: int, options: list[str]) -> str:
    lowered = question.lower()
    if index == 0 and options:
        return "single_select"
    if any(token in question for token in ("??", "??", "??")) or lowered.startswith("is "):
        return "confirm"
    return "input"


def _is_renderable_intervention_question(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    normalized = text.lower()
    if re.fullmatch(r"[-—–•_\s]+", text):
        return False
    explanatory_patterns = (
        "请提供以下",
        "请补充以下",
        "我需要更多信息",
        "我需要以下",
        "需要以下",
        "为了帮您",
        "为了帮助您",
        "为了给您",
        "为了继续",
        "包括",
        "如下",
        "基础信息",
        "关键信息",
        "please provide the following",
        "please answer the following",
        "i need more information",
        "i need the following",
        "to continue",
        "to help",
        "the following details",
        "key information",
        "basic information",
    )
    if any(pattern in text for pattern in explanatory_patterns):
        return False
    if text.endswith(":") or text.endswith("："):
        return False
    if any(token in text for token in ("？", "?")):
        return True
    return bool(
        re.match(
            r"^(请问|请填写|请提供|请补充|请输入|请选择|是否|能否|可否|what\b|when\b|where\b|who\b|which\b|how\b)",
            normalized,
        )
    )


def _build_intervention_questions(question: str, options: list[str]) -> list[dict[str, Any]]:
    question_parts = [
        part
        for part in _extract_clarification_questions(question)
        if _is_renderable_intervention_question(part)
    ]
    if len(question_parts) < 2:
        return []

    questions: list[dict[str, Any]] = []
    for index, part in enumerate(question_parts):
        kind = _infer_question_kind(part, index=index, options=options)
        entry: dict[str, Any] = {
            "key": f"question_{index + 1}",
            "label": part,
            "kind": kind,
            "required": True,
        }
        if kind == "single_select":
            entry["options"] = _build_intervention_options(options)
            entry["min_select"] = 1
            entry["max_select"] = 1
        elif kind == "input":
            entry["placeholder"] = part
        elif kind == "confirm":
            entry["confirm_text"] = "?????"
        questions.append(entry)
    return questions


def _build_help_request_intervention(
    parent_task: TaskStatus,
    help_request: HelpRequestPayload,
    *,
    agent_name: str,
) -> InterventionRequest:
    question = str(help_request.get("clarification_question") or "").strip()
    context = str(help_request.get("clarification_context") or "").strip()
    options = _normalize_clarification_options(help_request.get("clarification_options"))
    interaction_kind = _resolve_user_interaction_kind(help_request, options)
    questions = _build_intervention_questions(question, options)
    request_id = f"intv_{uuid.uuid4().hex[:12]}"
    fingerprint = f"fp_{uuid.uuid4().hex[:12]}"
    title = question or "??????"
    reason = context or help_request.get("reason", "").strip() or title

    action: dict[str, Any] = {
        "key": "submit_response",
        "label": "?????" if interaction_kind == "confirm" else "?????",
        "kind": "composite" if questions else interaction_kind,
        "resolution_behavior": "resume_current_task",
        "required": True,
    }

    if questions:
        action["confirm_text"] = "?????"
    elif interaction_kind == "input":
        action["placeholder"] = question or "??????????"
        action["confirm_text"] = "?????"
    elif interaction_kind == "confirm":
        action["confirm_text"] = "?????"
    elif interaction_kind == "single_select":
        action["options"] = _build_intervention_options(options)
        action["min_select"] = 1
        action["max_select"] = 1
        action["confirm_text"] = "????"
    elif interaction_kind == "multi_select":
        action["options"] = _build_intervention_options(options)
        action["min_select"] = 1
        action["max_select"] = len(options)
        action["confirm_text"] = "????"

    return {
        "request_id": request_id,
        "fingerprint": fingerprint,
        "intervention_type": "clarification",
        "title": title,
        "reason": reason,
        "description": context or None,
        "source_agent": agent_name,
        "source_task_id": parent_task["task_id"],
        "category": "user_clarification",
        "action_summary": title,
        "context": help_request.get("context_payload"),
        "action_schema": {
            "actions": [action],
        },
        "questions": questions or None,
        "created_at": _utc_now_iso(),
    }


def _should_interrupt_for_user_clarification(help_request: HelpRequestPayload) -> bool:
    strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
    return strategy in {"user_clarification", "user_confirmation", "user_multi_select"} or bool(str(help_request.get("clarification_question") or "").strip())


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


def _build_clarification_request(
    parent_task: TaskStatus,
    help_request: HelpRequestPayload,
) -> ClarificationRequest | None:
    question = str(help_request.get("clarification_question") or "").strip()
    context = str(help_request.get("clarification_context") or "").strip()
    questions = [
        {
            "key": f"clarification_{index + 1}",
            "label": item,
            "kind": "input",
            "required": True,
            "placeholder": item,
            "help_text": None,
        }
        for index, item in enumerate(_extract_clarification_questions(question))
        if _is_renderable_intervention_question(item)
    ]
    if not questions:
        return None

    title = context or question or parent_task["description"]
    description = context or "请补充以下信息后继续执行"
    return {
        "title": title,
        "description": description,
        "questions": questions,
    }


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


def _collect_dependency_result_sources(
    dependency_ids: list[str],
    task_pool: list[TaskStatus],
    verified_facts: dict[str, Any],
) -> dict[str, str]:
    sources: dict[str, str] = {}
    tasks_by_id = {task["task_id"]: task for task in task_pool}
    for dependency_id in dependency_ids:
        task = tasks_by_id.get(dependency_id)
        if task is None:
            continue
        if dependency_id in verified_facts:
            sources[dependency_id] = "verified_fact"
        elif task.get("result") is not None:
            sources[dependency_id] = "task_result"
        else:
            sources[dependency_id] = "unknown"
    return sources


def _interrupt_for_clarification(
    parent_task: TaskStatus,
    prompt: str,
    route_count: int,
    writer,
    *,
    agent_name: str,
    status_detail: str,
    clarification_request: ClarificationRequest | None = None,
) -> dict:
    resumed_task: TaskStatus = {
        **parent_task,
        "status": "RUNNING",
        "request_help": None,
        "depends_on_task_ids": [],
        "blocked_reason": None,
        "clarification_prompt": prompt,
        "clarification_request": clarification_request,
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
        clarification_request=clarification_request,
        status_detail=status_detail,
    )
    return {
        "task_pool": [resumed_task],
        "execution_state": "INTERRUPTED",
        "route_count": route_count,
        "messages": [AIMessage(content=prompt, name="ask_clarification")],
    }


def _interrupt_for_intervention(
    parent_task: TaskStatus,
    intervention_request: InterventionRequest,
    route_count: int,
    writer,
    *,
    agent_name: str,
) -> dict:
    interrupted_task: TaskStatus = {
        **parent_task,
        "status": "WAITING_INTERVENTION",
        "request_help": None,
        "depends_on_task_ids": [],
        "blocked_reason": None,
        "clarification_prompt": None,
        "clarification_request": None,
        "intervention_request": intervention_request,
        "intervention_status": "pending",
        "intervention_fingerprint": intervention_request["fingerprint"],
        "intervention_resolution": None,
        "status_detail": "@waiting_intervention",
        "updated_at": _utc_now_iso(),
    }
    _emit_task_event(
        writer,
        "task_waiting_intervention",
        interrupted_task,
        agent_name,
        status="waiting_intervention",
        status_detail="@waiting_intervention",
        intervention_request=intervention_request,
        intervention_status="pending",
        intervention_fingerprint=intervention_request["fingerprint"],
    )
    return {
        "task_pool": [interrupted_task],
        "execution_state": "INTERRUPTED",
        "route_count": route_count,
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
        "clarification_request": None,
        "status_detail": "@dependency_resolved",
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
        agent_name = parent_task.get("assigned_agent") or "workflow-router"
        options = _normalize_clarification_options(help_request.get("clarification_options"))
        logger.info(
            "[Router] Direct user clarification required for parent_task=%s run_id=%s resolution_strategy=%s options=%s",
            parent_task["task_id"],
            parent_task.get("run_id"),
            help_request.get("resolution_strategy"),
            help_request.get("clarification_options"),
        )
        if options or str(help_request.get("resolution_strategy") or "").strip().lower() in {"user_confirmation", "user_multi_select"}:
            intervention_request = _build_help_request_intervention(
                parent_task,
                help_request,
                agent_name=agent_name,
            )
            return _interrupt_for_intervention(
                parent_task,
                intervention_request,
                route_count,
                writer,
                agent_name=agent_name,
            )
        prompt = _build_user_clarification_prompt(parent_task, help_request)
        clarification_request = _build_clarification_request(parent_task, help_request)
        return _interrupt_for_clarification(
            parent_task,
            prompt,
            route_count,
            writer,
            agent_name=agent_name,
            status_detail="@waiting_clarification",
            clarification_request=clarification_request,
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
                status_detail=f"@retrying_helper:{direct_candidate}",
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
            status_detail="@waiting_clarification",
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
            status_detail="@waiting_clarification",
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
            _append_candidate_hints(_build_helper_context(help_request), hinted),
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
            status_detail="@waiting_clarification",
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
    writer = _get_event_writer()
    running = [t for t in task_pool if t["status"] == "RUNNING"]

    if running:
        running_task = running[0]
        run_id = _resolve_run_id(state, running_task)
        logger.info(
            "[Router] Found RUNNING task, forwarding to executor. run_id=%s task_id=%s assigned_agent=%s "
            "status_detail=%r resolved_input_keys=%s resume_count=%s",
            run_id,
            running_task["task_id"],
            running_task.get("assigned_agent"),
            running_task.get("status_detail"),
            list((running_task.get("resolved_inputs") or {}).keys()),
            running_task.get("resume_count"),
        )
        detail = _build_executing_detail(running_task)
        _emit_workflow_stage(writer, "executing", detail, run_id=run_id)
        return {
            "execution_state": "ROUTING_DONE",
            "route_count": route_count,
            "run_id": run_id,
            **_build_workflow_stage_update("executing", detail),
        }

    # WAITING_INTERVENTION tasks: check if the latest message resolves one.
    waiting_intervention = [t for t in task_pool if t["status"] == "WAITING_INTERVENTION"]
    if waiting_intervention:
        messages = state.get("messages") or []
        latest_msg = messages[-1] if messages else None
        if latest_msg and is_intervention_resolution_message(latest_msg):
            # The user already resolved this intervention via the Gateway endpoint.
            # The update_state() from the Gateway may have created a branch checkpoint
            # that this graph run didn't pick up, so we apply the transition here.
            task = waiting_intervention[0]
            intervention_request = task.get("intervention_request") or {}
            intervention_resolution = task.get("intervention_resolution")

            # If the Gateway's update_state was visible, intervention_resolution would
            # already be set.  If not, reconstruct the resolution from the message.
            if not isinstance(intervention_resolution, dict):
                # Parse the resolution from the [intervention_resolved] message text
                msg_text = getattr(latest_msg, "content", "") if latest_msg else ""
                import re as _re
                req_id_match = _re.search(r"request_id=(\S+)", msg_text)
                action_key_match = _re.search(r"action_key=(\S+)", msg_text)
                request_id = req_id_match.group(1) if req_id_match else intervention_request.get("request_id", "")
                action_key = action_key_match.group(1) if action_key_match else "approve"

                intervention_resolution: InterventionResolution = {
                    "request_id": request_id,
                    "fingerprint": intervention_request.get("fingerprint", ""),
                    "action_key": action_key,
                    "payload": {},
                }

            # Determine resolution behavior from action_schema
            action_key = intervention_resolution.get("action_key", "approve")
            action_schema = intervention_request.get("action_schema", {})
            actions = action_schema.get("actions", [])
            resolution_behavior = "resume_current_task"
            for action in actions:
                if action.get("key") == action_key:
                    resolution_behavior = action.get("resolution_behavior", "resume_current_task")
                    break

            if resolution_behavior == "fail_current_task":
                new_status = "FAILED"
                status_detail = "@failed"
            else:
                new_status = "RUNNING"
                status_detail = "@intervention_resolved"

            # Build resolved_inputs with intervention_resolution info
            existing_resolved = dict(task.get("resolved_inputs") or {})
            existing_resolved["intervention_resolution"] = {
                "action_key": action_key,
                "payload": intervention_resolution.get("payload", {}),
                "resolution_behavior": resolution_behavior,
            }

            updated_task: TaskStatus = {
                **task,
                "status": new_status,
                "intervention_status": "resolved",
                "intervention_resolution": intervention_resolution,
                "resolved_inputs": existing_resolved,
                "status_detail": status_detail,
                "updated_at": _utc_now_iso(),
            }

            run_id = _resolve_run_id(state, task)
            logger.info(
                "[Router] Resolved intervention in-graph: task_id=%s request_id=%s action_key=%s behavior=%s new_status=%s",
                task["task_id"],
                intervention_resolution.get("request_id"),
                action_key,
                resolution_behavior,
                new_status,
            )
            _emit_task_event(writer, "task_resumed", updated_task, task.get("assigned_agent", ""), status_detail=status_detail)

            if new_status == "RUNNING":
                detail = _build_executing_detail(updated_task)
                _emit_workflow_stage(writer, "executing", detail, run_id=run_id)
                return {
                    "task_pool": [updated_task],
                    "execution_state": "ROUTING_DONE",
                    "route_count": route_count,
                    "run_id": run_id,
                    **_build_workflow_stage_update("executing", detail),
                }
            else:
                return {
                    "task_pool": [updated_task],
                    "execution_state": "EXECUTING_DONE",
                    "route_count": route_count,
                }

        logger.info("[Router] Found %d WAITING_INTERVENTION task(s), waiting for user resolution.", len(waiting_intervention))
        return {
            "execution_state": "INTERRUPTED",
            "route_count": route_count,
        }

    waiting = [t for t in task_pool if t["status"] == "WAITING_DEPENDENCY"]
    verified_facts = state.get("verified_facts") or {}
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
                            status_detail=f"@retrying_helper:{direct_candidate}",
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
                    status_detail="@waiting_clarification",
                )
            resumed_task = _resume_parent_from_helper(waiting_task, dependency_ids, task_pool, verified_facts)
            if resumed_task is not None:
                dependency_sources = _collect_dependency_result_sources(dependency_ids, task_pool, verified_facts)
                logger.info(
                    "[Router] Resuming parent task '%s' run_id=%s depends_on=%s sources=%s resolved_input_keys=%s resume_count=%s",
                    waiting_task["task_id"],
                    resumed_task.get("run_id"),
                    dependency_ids,
                    dependency_sources,
                    list((resumed_task.get("resolved_inputs") or {}).keys()),
                    resumed_task.get("resume_count"),
                )
                _emit_task_event(
                    writer,
                    "task_resumed",
                    resumed_task,
                    resumed_task.get("assigned_agent") or "workflow-router",
                    status="in_progress",
                    status_detail="@dependency_resolved",
                    resolved_inputs=resumed_task.get("resolved_inputs"),
                    resume_count=resumed_task.get("resume_count"),
                )
                return {
                    "task_pool": [resumed_task],
                    "execution_state": "ROUTING_DONE",
                    "route_count": route_count,
                    "run_id": resumed_task.get("run_id"),
                    **_build_workflow_stage_update(
                        "executing",
                        _build_executing_detail(resumed_task),
                    ),
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
        "status_detail": f"@assigned:{assigned}",
        "clarification_prompt": None,
        "clarification_request": None,
        "updated_at": _utc_now_iso(),
    }
    detail = _build_executing_detail(updated_task)
    _emit_workflow_stage(writer, "executing", detail, run_id=run_id)
    return {
        "task_pool": [updated_task],
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
        **_build_workflow_stage_update("executing", detail),
    }
