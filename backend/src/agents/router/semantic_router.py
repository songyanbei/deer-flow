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

from src.agents.intervention.decision_cache import (
    build_cached_intervention_entry,
    increment_cache_reuse_count,
    is_intervention_cache_valid,
)
from src.agents.intervention.fingerprint import generate_clarification_semantic_fingerprint
from src.agents.intervention.help_request_builder import (
    build_help_request_intervention,
    normalize_clarification_options,
    resolve_user_interaction_kind,
    should_interrupt_for_user_clarification,
)
from src.agents.thread_state import (
    ClarificationRequest,
    HelpRequestPayload,
    InterventionRequest,
    InterventionResolution,
    TaskStatus,
    ThreadState,
    WorkflowStage,
)
from src.agents.workflow_resume import (
    apply_intervention_resolution,
    build_intervention_resolution_record,
    build_intervention_resolved_inputs_entry,
    extract_latest_clarification_answer,
    is_intervention_resolution_message,
)
from src.agents.scheduler import get_blocked_by_failed_dependency, select_execution_batch
from src.config.agents_config import list_domain_agents
from src.config.paths import resolve_tenant_agents_dir
from src.models import create_chat_model
from src.observability import record_decision

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

    Prefer ``expected_output`` or ``required_capability`` so the description
    reflects only the *dependency* the helper must fulfill, not the parent's
    full business goal (which lives in ``problem``).
    """
    for key in ("expected_output", "required_capability", "problem"):
        value = (help_request.get(key) or "").strip()  # type: ignore[arg-type]
        if value:
            return value
    return "协助处理依赖任务"


def _build_helper_context(help_request: HelpRequestPayload) -> str:
    """Build a scope-limited prompt for the helper agent.

    Structure:
    1. **Task** — ``required_capability`` + ``expected_output`` (what to do).
    2. **Reference context** (read-only) — the parent's ``problem`` is kept so
       the helper can see entity names / time ranges, but labelled as *do-not-act-on*.
    3. **Background** — ``reason`` (why the parent cannot do this itself).
    4. **Bilingual scope constraint** — hard instruction in both Chinese and English.
    """
    lines: list[str] = []

    # ── Core task (what the helper must do) ──
    for key in ("required_capability", "expected_output"):
        value = (help_request.get(key) or "").strip()  # type: ignore[arg-type]
        if value:
            lines.append(f"{key}: {value}")

    # ── Reference context (read-only, preserves entity info) ──
    problem = (help_request.get("problem") or "").strip()
    if problem:
        lines.append(f"\nreference_context (read-only, do NOT act on): {problem}")

    # ── Background (why the requester cannot do it) ──
    reason = (help_request.get("reason") or "").strip()
    if reason:
        lines.append(f"background: {reason}")

    # ── Bilingual scope constraint ──
    lines.append(
        "\n【范围约束】你只需完成上面 required_capability / expected_output 描述的工作。"
        "拿到结果后立即返回，不要继续处理 reference_context 中提到的其他业务目标。"
        "\nSCOPE: Only fulfil required_capability / expected_output above. "
        "Return the result immediately once obtained. "
        "Do NOT pursue any other goals from reference_context."
    )
    return "\n".join(lines)


def _append_candidate_hints(task_description: str, hinted_agents: list[str]) -> str:
    if not hinted_agents:
        return task_description
    hinted = ", ".join(hinted_agents)
    return f"{task_description}\nPreferred helper candidates (hint only): {hinted}"


def _get_helper_candidates(help_request: HelpRequestPayload, requester: str | None, *, agents_dir=None, allowed_agents=None) -> tuple[list, list[str], list[str]]:
    domain_agents = list_domain_agents(agents_dir=agents_dir, allowed_agents=allowed_agents)
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


# ---------------------------------------------------------------------------
# Scope-loop detection & force-complete
# ---------------------------------------------------------------------------

def _detect_scope_loop(
    helper_task: TaskStatus,
    proposed_agent: str,
    task_pool: list[TaskStatus],
) -> bool:
    """Return True if routing *proposed_agent* for *helper_task* would create
    a scope loop — i.e. the helper is trying to delegate work back to its
    grandparent's (or any ancestor's) domain agent.

    This catches the pattern:
        hr-agent → contacts-agent(helper) → hr-agent(sub-helper)
    where contacts-agent should have returned its result instead of
    continuing to pursue the parent's business goal.
    """
    task_index = {t["task_id"]: t for t in task_pool}
    ancestor_id = helper_task.get("parent_task_id")
    visited: set[str] = set()
    while ancestor_id and ancestor_id not in visited:
        visited.add(ancestor_id)
        ancestor = task_index.get(ancestor_id)
        if ancestor is None:
            break
        if ancestor.get("assigned_agent") == proposed_agent:
            return True
        ancestor_id = ancestor.get("parent_task_id")
    return False


def _extract_helper_partial_result(helper_task: TaskStatus) -> str:
    """Best-effort extraction of useful output from a helper that will be
    force-completed due to scope-loop.

    Priority:
    1. ``context_payload`` from the blocked request_help  (structured data).
    2. ``reason`` field (often contains accomplished facts, e.g. "I have the
       employee's openId (ou_mock_10033)").
    3. Generic fallback.
    """
    rh = helper_task.get("request_help") or {}
    ctx_payload = rh.get("context_payload")
    if isinstance(ctx_payload, dict) and ctx_payload:
        return json.dumps(ctx_payload, ensure_ascii=False)
    reason = (rh.get("reason") or "").strip()
    if reason:
        return reason
    return "Helper completed with partial results (scope-loop auto-complete)."


def _force_complete_helper_scope_loop(
    helper_task: TaskStatus,
    proposed_agent: str,
    state: ThreadState,
    route_count: int,
) -> dict:
    """Force-complete a helper task that would create a scope loop.

    Instead of spawning a sub-helper that routes back to an ancestor agent,
    mark this helper as DONE with whatever partial result it already has.
    The normal dependency-resolution flow will then inject this result into
    the parent task and resume it.
    """
    run_id = _resolve_run_id(state, helper_task)
    partial_result = _extract_helper_partial_result(helper_task)

    logger.warning(
        "[Router] Scope-loop detected: helper task '%s' (%s) tried to route to '%s' "
        "which is an ancestor agent. Force-completing helper with partial result.",
        helper_task["task_id"],
        helper_task.get("assigned_agent"),
        proposed_agent,
    )
    record_decision(
        "scope_loop_auto_complete",
        run_id=run_id,
        task_id=helper_task["task_id"],
        agent_name=helper_task.get("assigned_agent"),
        inputs={
            "proposed_helper_agent": proposed_agent,
            "parent_task_id": helper_task.get("parent_task_id"),
        },
        output={"partial_result_len": len(partial_result)},
        reason="helper tried to delegate back to ancestor agent",
    )

    completed_task: TaskStatus = {
        **helper_task,
        "status": "DONE",
        "result": partial_result,
        "request_help": None,
        "blocked_reason": None,
        "status_detail": "@scope_loop_auto_complete",
        "updated_at": _utc_now_iso(),
    }
    return {
        "task_pool": [completed_task],
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
    }


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
    # Carry upstream structured context so the helper agent can see
    # facts already known by the requester (e.g. organizer name),
    # avoiding redundant clarification questions.
    _MAX_CONTEXT_PAYLOAD_CHARS = 2000
    context_payload = (parent_task.get("request_help") or {}).get("context_payload")
    if isinstance(context_payload, dict) and context_payload:
        serialized = json.dumps(context_payload, ensure_ascii=False)
        if len(serialized) <= _MAX_CONTEXT_PAYLOAD_CHARS:
            helper_task["resolved_inputs"] = {"upstream_context": context_payload}
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
    record_decision(
        "helper_dispatch",
        run_id=run_id,
        task_id=parent_task["task_id"],
        agent_name=assigned,
        inputs={"parent_task_id": parent_task["task_id"], "help_request": (parent_task.get("request_help") or {}).get("problem", "")[:300], "requester": parent_task.get("requested_by_agent")},
        output={"helper_task_id": helper_task_id, "assigned_agent": assigned},
    )
    return {
        "task_pool": [updated_parent, helper_task],
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
    }


def _normalize_clarification_options(options: Any) -> list[str]:
    return normalize_clarification_options(options)


def _build_intervention_options(options: list[str]) -> list[dict[str, str]]:
    from src.agents.intervention.help_request_builder import build_intervention_options
    return build_intervention_options(options)


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
    return resolve_user_interaction_kind(help_request, options)


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
    """Compatibility wrapper — delegates to shared builder.

    The router retains this path as a fallback for old checkpoints or
    legacy state recovery where a WAITING_DEPENDENCY task was already
    persisted before executor normalization was available.
    """
    return build_help_request_intervention(parent_task, help_request, agent_name=agent_name)


def _should_interrupt_for_user_clarification(help_request: HelpRequestPayload) -> bool:
    return should_interrupt_for_user_clarification(help_request)


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


def _apply_before_interrupt_emit_safe(
    *,
    interrupt_type: str,
    task: dict[str, Any],
    agent_name: str,
    source_path: str,
    proposed_update: dict[str, Any],
    state: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Wrapper around lifecycle.apply_before_interrupt_emit with fail-closed error handling."""
    try:
        from src.agents.hooks.lifecycle import apply_before_interrupt_emit
        return apply_before_interrupt_emit(
            interrupt_type=interrupt_type,
            task=task,
            agent_name=agent_name,
            source_path=source_path,
            proposed_update=proposed_update,
            state=state or {},
            run_id=run_id,
        )
    except Exception as exc:
        logger.error("[Router] before_interrupt_emit hook error at %s: %s", source_path, exc)
        return {
            "execution_state": "ERROR",
            "final_result": f"Runtime hook error (before_interrupt_emit at {source_path}): {exc}",
        }


def _interrupt_for_clarification(
    parent_task: TaskStatus,
    prompt: str,
    route_count: int,
    writer,
    *,
    agent_name: str,
    status_detail: str,
    clarification_request: ClarificationRequest | None = None,
    state: dict[str, Any] | None = None,
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
    _candidate_return: dict[str, Any] = {
        "task_pool": [resumed_task],
        "execution_state": "INTERRUPTED",
        "route_count": route_count,
        "messages": [AIMessage(content=prompt, name="ask_clarification")],
    }
    _candidate_return = _apply_before_interrupt_emit_safe(
        interrupt_type="clarification",
        task=resumed_task,
        agent_name=agent_name,
        source_path="router._interrupt_for_clarification",
        proposed_update=_candidate_return,
        state=state,
        run_id=parent_task.get("run_id"),
    )
    if _candidate_return.get("execution_state") == "ERROR":
        return _candidate_return
    # Use the potentially hook-modified task for event emission
    _effective_task = _candidate_return.get("task_pool", [resumed_task])[0] if _candidate_return.get("task_pool") else resumed_task
    _emit_task_event(
        writer,
        "task_running",
        _effective_task,
        agent_name,
        status="waiting_clarification",
        clarification_prompt=prompt,
        clarification_request=clarification_request,
        status_detail=status_detail,
    )
    return _candidate_return


def _interrupt_for_intervention(
    parent_task: TaskStatus,
    intervention_request: InterventionRequest,
    route_count: int,
    writer,
    *,
    agent_name: str,
    state: dict[str, Any] | None = None,
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
        "continuation_mode": "continue_after_intervention",
        "pending_interrupt": {
            "interrupt_type": "intervention",
            "interrupt_kind": intervention_request.get("interrupt_kind"),
            "request_id": intervention_request.get("request_id"),
            "fingerprint": intervention_request.get("fingerprint"),
            "semantic_key": intervention_request.get("semantic_key"),
            "source_signal": intervention_request.get("source_signal") or "request_help",
            "source": agent_name,
            "source_agent": agent_name,
            "created_at": _utc_now_iso(),
        },
        "pending_tool_call": None,
        "updated_at": _utc_now_iso(),
    }
    _candidate_return: dict[str, Any] = {
        "task_pool": [interrupted_task],
        "execution_state": "INTERRUPTED",
        "route_count": route_count,
    }
    _candidate_return = _apply_before_interrupt_emit_safe(
        interrupt_type="intervention",
        task=interrupted_task,
        agent_name=agent_name,
        source_path="router._interrupt_for_intervention",
        proposed_update=_candidate_return,
        state=state,
        run_id=parent_task.get("run_id"),
    )
    if _candidate_return.get("execution_state") == "ERROR":
        return _candidate_return
    # Use the potentially hook-modified task for event emission
    _effective_task = _candidate_return.get("task_pool", [interrupted_task])[0] if _candidate_return.get("task_pool") else interrupted_task
    _emit_task_event(
        writer,
        "task_waiting_intervention",
        _effective_task,
        agent_name,
        status="waiting_intervention",
        status_detail="@waiting_intervention",
        intervention_request=intervention_request,
        intervention_status="pending",
        intervention_fingerprint=intervention_request["fingerprint"],
    )
    return _candidate_return


def _auto_resolve_intervention_from_cache(
    parent_task: TaskStatus,
    *,
    cached: dict[str, Any],
    semantic_fp: str,
    intervention_cache: dict[str, dict[str, Any]],
    route_count: int,
    writer,
    agent_name: str,
) -> dict:
    updated_entry = increment_cache_reuse_count(cached)
    updated_cache = {**intervention_cache, semantic_fp: updated_entry}

    cached_resolution = build_intervention_resolution_record(
        request_id=f"cache:{semantic_fp}",
        fingerprint=semantic_fp,
        action_key=updated_entry["action_key"],
        payload=updated_entry.get("payload", {}),
        resolution_behavior=updated_entry.get("resolution_behavior", "resume_current_task"),
    )
    resolved_inputs = dict(parent_task.get("resolved_inputs") or {})
    resolved_inputs["intervention_resolution"] = build_intervention_resolved_inputs_entry(cached_resolution)

    resumed_task: TaskStatus = {
        **parent_task,
        "status": "RUNNING",
        "request_help": None,
        "depends_on_task_ids": [],
        "blocked_reason": None,
        "clarification_prompt": None,
        "clarification_request": None,
        "intervention_resolution": cached_resolution,
        "resolved_inputs": resolved_inputs,
        "status_detail": "@cache_auto_resolved",
        "continuation_mode": "continue_after_intervention",
        "resume_count": int(parent_task.get("resume_count") or 0) + 1,
        "pending_interrupt": None,
        "pending_tool_call": None,
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
        "route_count": route_count,
        "intervention_cache": updated_cache,
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

    # ── Compatibility / fallback path ──
    # After the executor normalization refactor, user-owned help requests
    # should arrive here already as WAITING_INTERVENTION.  This branch is
    # retained for old checkpoints or edge cases where a WAITING_DEPENDENCY
    # task was persisted before executor normalization was deployed.  New
    # runtime flow should NOT rely on this router reclassification.
    if _should_interrupt_for_user_clarification(help_request):
        agent_name = parent_task.get("assigned_agent") or "workflow-router"
        options = _normalize_clarification_options(help_request.get("clarification_options"))
        question = str(help_request.get("clarification_question") or "").strip()
        intervention_cache = state.get("intervention_cache") or {}
        logger.info(
            "[Router] Compatibility path: user clarification for WAITING_DEPENDENCY parent_task=%s "
            "run_id=%s resolution_strategy=%s options=%s",
            parent_task["task_id"],
            parent_task.get("run_id"),
            help_request.get("resolution_strategy"),
            help_request.get("clarification_options"),
        )
        strategy = str(help_request.get("resolution_strategy") or "").strip().lower()
        if question or options or strategy in {"user_confirmation", "user_multi_select"}:
            semantic_fp = generate_clarification_semantic_fingerprint(agent_name, question, options)
            cached = intervention_cache.get(semantic_fp)
            if cached:
                if is_intervention_cache_valid(cached, require_resume_behavior=False):
                    logger.info(
                        "[Router] [Cache HIT] clarification semantic_fp=%s reuse_count=%s/%s",
                        semantic_fp,
                        cached.get("reuse_count", 0),
                        cached.get("max_reuse", -1),
                    )
                    return _auto_resolve_intervention_from_cache(
                        parent_task,
                        cached=cached,
                        semantic_fp=semantic_fp,
                        intervention_cache=intervention_cache,
                        route_count=route_count,
                        writer=writer,
                        agent_name=agent_name,
                    )
                max_reuse = cached.get("max_reuse", -1)
                reuse_count = cached.get("reuse_count", 0)
                if max_reuse != -1 and reuse_count >= max_reuse:
                    logger.info(
                        "[Router] [Cache EXPIRED] clarification semantic_fp=%s reuse_count=%s reached max_reuse=%s",
                        semantic_fp,
                        reuse_count,
                        max_reuse,
                    )

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
                state=dict(state) if state else None,
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
            state=dict(state) if state else None,
        )

    requester = parent_task.get("requested_by_agent")
    _cfg_help = config.get("configurable", {})
    _tenant_id = _cfg_help.get("tenant_id", "default")
    _agents_dir = resolve_tenant_agents_dir(_tenant_id)
    _allowed_agents = _cfg_help.get("allowed_agents")
    domain_agents, candidate_names, hinted = _get_helper_candidates(help_request, requester, agents_dir=_agents_dir, allowed_agents=_allowed_agents)
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
            record_decision(
                "helper_retry",
                run_id=parent_task.get("run_id"),
                task_id=parent_task["task_id"],
                agent_name=direct_candidate,
                inputs={"parent_task_id": parent_task["task_id"], "failed_helper_task_id": ""},
                output={"new_helper_task_id": "", "retry_count": next_retry_count},
                reason=budget_reason,
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
        record_decision(
            "budget_escalation",
            run_id=parent_task.get("run_id"),
            task_id=parent_task["task_id"],
            inputs={"route_count": route_count, "help_depth": help_depth, "resume_count": resume_count},
            reason=budget_reason,
        )
        prompt = _build_clarification_prompt(parent_task, help_request)
        return _interrupt_for_clarification(
            parent_task,
            prompt,
            route_count,
            writer,
            agent_name=parent_task.get("assigned_agent") or "workflow-router",
            status_detail="@waiting_clarification",
            state=dict(state) if state else None,
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
            state=dict(state) if state else None,
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
            state=dict(state) if state else None,
        )

    # ── Scope-loop guard ──
    # If this task is itself a helper (has parent_task_id) and the proposed
    # agent is an ancestor in the helper chain, force-complete this helper
    # with its partial results instead of creating a sub-helper loop.
    if parent_task.get("parent_task_id"):
        task_pool: list[TaskStatus] = state.get("task_pool") or []
        if _detect_scope_loop(parent_task, assigned, task_pool):
            return _force_complete_helper_scope_loop(
                parent_task, assigned, state, route_count,
            )

    return _route_to_helper(parent_task, state, route_count, assigned)


async def router_node(state: ThreadState, config: RunnableConfig) -> dict:
    _cfg = config.get("configurable", {})
    tenant_id = _cfg.get("tenant_id", "default")
    agents_dir = resolve_tenant_agents_dir(tenant_id)
    allowed_agents = _cfg.get("allowed_agents")
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
        # --- Bind clarification answers to tasks before forwarding to executor ---
        # When resuming after clarification, the user's answer is in global messages.
        # We extract it once and write it onto the specific task's resolved_inputs so
        # that executor reads from the task itself — never from global state.
        clarification_tasks = [t for t in running if t.get("continuation_mode") == "continue_after_clarification"]
        updated_clarification_tasks: list[TaskStatus] = []
        if clarification_tasks:
            clarification_answer = extract_latest_clarification_answer(state, config)
            if clarification_answer:
                # Bind the answer to the first clarification task only.
                # The user can only answer one clarification per graph resume;
                # remaining clarification tasks keep their mode and wait for the next round.
                target = clarification_tasks[0]
                existing_inputs = dict(target.get("resolved_inputs") or {})
                existing_inputs["clarification_answer"] = clarification_answer
                updated_target: TaskStatus = {**target, "resolved_inputs": existing_inputs}
                updated_clarification_tasks.append(updated_target)
                logger.info(
                    "[Router] Bound clarification answer to task_id=%s (len=%d).",
                    target["task_id"], len(clarification_answer),
                )

        running_task = running[0]
        run_id = _resolve_run_id(state, running_task)
        logger.info(
            "[Router] Found %d RUNNING task(s), forwarding to executor. run_id=%s first_task_id=%s assigned_agent=%s "
            "status_detail=%r resolved_input_keys=%s resume_count=%s",
            len(running),
            run_id,
            running_task["task_id"],
            running_task.get("assigned_agent"),
            running_task.get("status_detail"),
            list((running_task.get("resolved_inputs") or {}).keys()),
            running_task.get("resume_count"),
        )
        detail = _build_executing_detail(running_task)
        _emit_workflow_stage(writer, "executing", detail, run_id=run_id)
        _routing_done_result: dict[str, Any] = {
            "execution_state": "ROUTING_DONE",
            "route_count": route_count,
            "run_id": run_id,
            **_build_workflow_stage_update("executing", detail),
        }
        if updated_clarification_tasks:
            _routing_done_result["task_pool"] = updated_clarification_tasks
        return _routing_done_result

    # WAITING_INTERVENTION tasks: check if the latest message resolves one.
    waiting_intervention = [t for t in task_pool if t["status"] == "WAITING_INTERVENTION"]
    if waiting_intervention:
        messages = state.get("messages") or []
        latest_msg = messages[-1] if messages else None
        if latest_msg and is_intervention_resolution_message(latest_msg):
            # The user already resolved this intervention via the Gateway endpoint.
            # The update_state() from the Gateway may have created a branch checkpoint
            # that this graph run didn't pick up, so we apply the transition here.

            # --- Precise task targeting (Phase 2 Stage 1) ---
            # Three-layer lookup: Gateway-written resolution > message request_id > single-task fallback.
            # Never guess when ambiguous — return INTERRUPTED instead.
            import re as _re
            msg_text = getattr(latest_msg, "content", "") if latest_msg else ""
            req_id_match = _re.search(r"request_id=(\S+)", msg_text)
            parsed_request_id = req_id_match.group(1) if req_id_match else None

            # Layer 1: Gateway already wrote intervention_resolution onto the correct task.
            task = next(
                (t for t in waiting_intervention if isinstance(t.get("intervention_resolution"), dict)),
                None,
            )
            # Layer 2: Match by request_id parsed from the [intervention_resolved] message.
            if task is None and parsed_request_id:
                task = next(
                    (t for t in waiting_intervention
                     if (t.get("intervention_request") or {}).get("request_id") == parsed_request_id),
                    None,
                )
            # Layer 3: Single waiting task — backward compatible with serial execution.
            if task is None and len(waiting_intervention) == 1:
                task = waiting_intervention[0]

            if task is None:
                logger.warning(
                    "[Router] Cannot match intervention resolution to any of %d waiting tasks. "
                    "parsed_request_id=%s waiting_task_ids=%s",
                    len(waiting_intervention),
                    parsed_request_id,
                    [t["task_id"] for t in waiting_intervention],
                )
                return {
                    "execution_state": "INTERRUPTED",
                    "route_count": route_count,
                }

            intervention_request = task.get("intervention_request") or {}
            intervention_resolution = task.get("intervention_resolution")
            intervention_cache = state.get("intervention_cache") or {}

            # If the Gateway's update_state was visible, intervention_resolution would
            # already be set.  If not, reconstruct the resolution from the message.
            if not isinstance(intervention_resolution, dict):
                action_key_match = _re.search(r"action_key=(\S+)", msg_text)
                request_id = parsed_request_id or intervention_request.get("request_id", "")
                action_key = action_key_match.group(1) if action_key_match else "approve"
                intervention_resolution = build_intervention_resolution_record(
                    request_id=request_id,
                    fingerprint=intervention_request.get("fingerprint", ""),
                    action_key=action_key,
                    payload={},
                    resolution_behavior="resume_current_task",
                )

            action_key = intervention_resolution.get("action_key", "approve")
            updated_task, resolution_error = apply_intervention_resolution(
                task,
                intervention_resolution,
                resolved_at=_utc_now_iso(),
            )
            if resolution_error is not None or updated_task is None:
                logger.warning(
                    "[Router] Failed to apply in-graph intervention resolution task_id=%s request_id=%s error=%s",
                    task["task_id"],
                    intervention_resolution.get("request_id"),
                    resolution_error,
                )
                return {
                    "execution_state": "INTERRUPTED",
                    "route_count": route_count,
                }
            resolution_behavior = updated_task.get("intervention_resolution", {}).get("resolution_behavior", "resume_current_task")
            semantic_fp, cache_entry = build_cached_intervention_entry(
                intervention_request,
                action_key=action_key,
                payload=intervention_resolution.get("payload", {}),
                resolution_behavior=resolution_behavior,
                resolved_at=updated_task["updated_at"],
            )
            updated_cache = intervention_cache
            if semantic_fp and cache_entry:
                updated_cache = {**intervention_cache, semantic_fp: cache_entry}
                logger.info(
                    "[Router] [Cache WRITE] semantic_fp=%s type=%s max_reuse=%s",
                    semantic_fp,
                    cache_entry.get("intervention_type"),
                    cache_entry.get("max_reuse"),
                )

            run_id = _resolve_run_id(state, task)
            record_decision(
                "intervention_resolution",
                run_id=run_id,
                task_id=task["task_id"],
                inputs={"task_id": task["task_id"], "request_id": intervention_resolution.get("request_id"), "action_key": action_key},
                output={"behavior": resolution_behavior, "new_status": updated_task["status"]},
            )
            logger.info(
                "[Router] Resolved intervention in-graph: task_id=%s request_id=%s action_key=%s behavior=%s new_status=%s",
                task["task_id"],
                intervention_resolution.get("request_id"),
                action_key,
                resolution_behavior,
                updated_task["status"],
            )
            _emit_task_event(writer, "task_resumed", updated_task, task.get("assigned_agent", ""), status_detail=updated_task.get("status_detail"))

            if updated_task["status"] == "RUNNING":
                detail = _build_executing_detail(updated_task)
                _emit_workflow_stage(writer, "executing", detail, run_id=run_id)
                _resolve_return: dict[str, Any] = {
                    "task_pool": [updated_task],
                    "execution_state": "ROUTING_DONE",
                    "route_count": route_count,
                    "run_id": run_id,
                    "intervention_cache": updated_cache,
                    **_build_workflow_stage_update("executing", detail),
                }
            else:
                _resolve_return = {
                    "task_pool": [updated_task],
                    "execution_state": "EXECUTING_DONE",
                    "route_count": route_count,
                    "intervention_cache": updated_cache,
                }

            try:
                from src.agents.hooks.lifecycle import apply_after_interrupt_resolve
                _resolve_return = apply_after_interrupt_resolve(
                    task=updated_task,
                    resolution=intervention_resolution,
                    source_path="router.in_graph_resolve",
                    proposed_update=_resolve_return,
                    state=dict(state) if state else {},
                    run_id=run_id,
                )
            except Exception as _hook_exc:
                logger.error("[Router] after_interrupt_resolve hook error: %s", _hook_exc)
                return {
                    "execution_state": "ERROR",
                    "final_result": f"Runtime hook error (after_interrupt_resolve): {_hook_exc}",
                }

            return _resolve_return

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
                    _, candidate_names, hinted = _get_helper_candidates(help_request, requester, agents_dir=agents_dir, allowed_agents=allowed_agents)
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
                    state=dict(state) if state else None,
                )
            resumed_task = _resume_parent_from_helper(waiting_task, dependency_ids, task_pool, verified_facts)
            if resumed_task is not None:
                dependency_sources = _collect_dependency_result_sources(dependency_ids, task_pool, verified_facts)
                record_decision(
                    "dependency_resolution",
                    run_id=resumed_task.get("run_id"),
                    task_id=waiting_task["task_id"],
                    inputs={"parent_task_id": waiting_task["task_id"], "resolved_input_keys": list((resumed_task.get("resolved_inputs") or {}).keys())},
                    output={"resume_status": "RUNNING", "assigned_agent": resumed_task.get("assigned_agent")},
                )
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

    # --- Phase 2 Stage 1: dependency-aware batch scheduling ---
    batch = select_execution_batch(task_pool)
    if not batch:
        # No runnable tasks — all pending tasks have unsatisfied deps.
        # First check if any pending tasks are permanently blocked by failed deps.
        blocked_by_failure = get_blocked_by_failed_dependency(task_pool)
        if blocked_by_failure:
            # Fail tasks whose dependencies have permanently failed.
            failed_updates: list[TaskStatus] = []
            for blocked_task in blocked_by_failure:
                failed_task: TaskStatus = {
                    **blocked_task,
                    "status": "FAILED",
                    "error": "Dependency task failed",
                    "status_detail": "@dependency_failed",
                    "updated_at": _utc_now_iso(),
                }
                failed_updates.append(failed_task)
                _emit_task_event(writer, "task_failed", failed_task, blocked_task.get("assigned_agent") or "", error="Dependency task failed")
            logger.info("[Router] Marked %d tasks as FAILED due to failed dependencies.", len(failed_updates))
            return {
                "task_pool": failed_updates,
                "execution_state": "EXECUTING_DONE",
                "route_count": route_count,
            }
        # Check if there are running/blocked tasks that might eventually unblock.
        if any(t["status"] in ("RUNNING", "WAITING_DEPENDENCY", "WAITING_INTERVENTION") for t in task_pool):
            logger.info("[Router] No runnable PENDING tasks, but active tasks exist — forwarding to executor.")
            return {"execution_state": "ROUTING_DONE", "route_count": route_count}
        logger.info("[Router] No runnable tasks and no active tasks, signaling planner.")
        return {"execution_state": "PLANNING_NEEDED", "route_count": route_count}

    domain_agents = list_domain_agents(agents_dir=agents_dir, allowed_agents=allowed_agents)
    valid_names = [a.name for a in domain_agents]
    agent_profiles = _build_agent_profiles(domain_agents)
    updated_tasks: list[TaskStatus] = []
    run_id: str | None = None

    for task in batch:
        task_run_id = _resolve_run_id(state, task)
        if run_id is None:
            run_id = task_run_id

        if task.get("assigned_agent") and task["assigned_agent"] in valid_names:
            assigned = task["assigned_agent"]
            logger.info("[Router] Fast path: task '%s' -> %s", task["task_id"], assigned)
            record_decision(
                "agent_route",
                run_id=task_run_id,
                task_id=task["task_id"],
                agent_name=assigned,
                inputs={"task_id": task["task_id"], "task_description": task["description"][:300]},
                output={"selected_agent": assigned},
                reason="fast_path_pre_assigned",
            )
        else:
            assigned = await _llm_route(task["description"], agent_profiles, valid_names, config)
            logger.info("[Router] LLM route: task '%s' -> %s", task["task_id"], assigned)
            record_decision(
                "agent_route" if assigned != "SYSTEM_FALLBACK" else "agent_route_fallback",
                run_id=task_run_id,
                task_id=task["task_id"],
                agent_name=assigned,
                inputs={"task_id": task["task_id"], "task_description": task["description"][:300], "candidates": valid_names},
                output={"selected_agent": assigned},
                reason="llm_route" if assigned != "SYSTEM_FALLBACK" else "no_suitable_agent",
                alternatives=[n for n in valid_names if n != assigned],
            )

        updated_task: TaskStatus = {
            **task,
            "run_id": task_run_id,
            "status": "RUNNING",
            "assigned_agent": assigned,
            "status_detail": f"@assigned:{assigned}",
            "clarification_prompt": None,
            "clarification_request": None,
            "updated_at": _utc_now_iso(),
        }
        updated_tasks.append(updated_task)

    if len(updated_tasks) > 1:
        logger.info(
            "[Router] Batch scheduled %d tasks for concurrent execution: %s",
            len(updated_tasks),
            [(t["task_id"], t.get("assigned_agent")) for t in updated_tasks],
        )

    detail = _build_executing_detail(updated_tasks[0])
    _emit_workflow_stage(writer, "executing", detail, run_id=run_id)
    for ut in updated_tasks:
        _emit_task_event(writer, "task_started", ut, ut.get("assigned_agent") or "", status_detail=ut.get("status_detail"))
    return {
        "task_pool": updated_tasks,
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
        **_build_workflow_stage_update("executing", detail),
    }
