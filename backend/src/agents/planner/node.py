"""Planner node: task decomposition and goal validation for the multi-agent graph."""

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.planner.prompt import DECOMPOSE_SYSTEM_PROMPT, VALIDATE_SYSTEM_PROMPT
from src.agents.thread_state import TaskStatus, ThreadState, VerifiedFact, WorkflowStage
from src.agents.workflow_resume import (
    extract_latest_user_input,
    is_human_message,
    latest_user_message_is_clarification_answer,
)
from src.config.agents_config import list_domain_agents
from src.models import create_chat_model

logger = logging.getLogger(__name__)
_NULLISH_TEXT_VALUES = {"none", "null", "undefined"}
_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _resolve_model(config: RunnableConfig) -> str | None:
    return config.get("configurable", {}).get("model_name") or config.get("configurable", {}).get("model")


def _build_agent_descriptions(agents) -> str:
    if not agents:
        return "(No domain agents configured)"
    return "\n".join(f"- {a.name}: {a.description}" for a in agents)


def _build_tasks_summary(task_pool: list[TaskStatus]) -> str:
    lines: list[str] = []
    for t in task_pool:
        status = t.get("status", "?")
        desc = t.get("description", "")
        agent = t.get("assigned_agent", "?")
        result = t.get("result") or t.get("error") or "(no result)"
        lines.append(f"[{status}] agent={agent}: {desc}\n  -> {result}")
    return "\n".join(lines) if lines else "(none)"


def _summarize_tasks_for_log(tasks: list[TaskStatus]) -> str:
    if not tasks:
        return "(none)"
    return " | ".join(
        f"{task['task_id']}:{task.get('assigned_agent') or '?'}:{task['status']}:{task['description'][:120]}"
        for task in tasks
    )


def _fact_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        if "summary" in value:
            return str(value["summary"])
        if "result" in value:
            return str(value["result"])
    return str(value)


def _build_facts_summary(verified_facts: VerifiedFact) -> str:
    if not verified_facts:
        return "(none)"
    lines: list[str] = []
    for idx, (fact_key, fact_value) in enumerate(verified_facts.items(), start=1):
        lines.append(f"{idx}. [{fact_key}] {_fact_value_to_text(fact_value)}")
    return "\n".join(lines)


def _try_repair_json(text: str) -> Any | None:
    """Attempt to repair JSON with unescaped double quotes inside string values.

    When an LLM produces JSON like {"summary": "主题为"产品介绍""},
    the interior quotes break json.loads.  This function iteratively
    finds the premature closing quote, escapes it, and retries.
    """
    max_attempts = 20
    for _ in range(max_attempts):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            pos = e.pos
            # Search backward from the error position for the unescaped quote
            # that prematurely closed a JSON string value.
            search_start = max(0, pos - 10)
            quote_pos = text.rfind('"', search_start, pos)
            if quote_pos > 0 and text[quote_pos - 1] != '\\':
                text = text[:quote_pos] + '\\"' + text[quote_pos + 1:]
            else:
                return None
    return None


def _extract_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    for match in _FENCED_BLOCK_RE.finditer(text):
        fenced = match.group(1).strip()
        if fenced:
            candidates.append(fenced)

    decoder = json.JSONDecoder()
    for start_char in ("[", "{"):
        start = 0
        while True:
            idx = text.find(start_char, start)
            if idx < 0:
                break
            snippet = text[idx:].lstrip()
            if not snippet:
                break
            try:
                parsed, end = decoder.raw_decode(snippet)
            except json.JSONDecodeError:
                start = idx + 1
                continue
            if isinstance(parsed, (dict, list)) and end > 0:
                if isinstance(parsed, dict) and not (
                    "done" in parsed
                    or "tasks" in parsed
                    or "summary" in parsed
                ):
                    start = idx + 1
                    continue
                extracted = snippet[:end].strip()
                if extracted:
                    candidates.append(extracted)
                break
            start = idx + 1

    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _parse_planner_output(raw: Any) -> dict:
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, list):
        text = " ".join(
            part.get("text", "")
            for part in raw
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    else:
        text = str(raw).strip()

    def _normalize_parsed(value: Any) -> dict:
        if isinstance(value, list):
            return {"done": False, "tasks": value, "parse_error": None}
        if isinstance(value, dict):
            value.setdefault("parse_error", None)
            return value
        return {"done": False, "tasks": [], "parse_error": "Planner output was not a JSON object or array."}

    # Prefer extracting JSON from common wrappers (e.g. ```json ...``` or leading prose).
    candidates = [text, *_extract_json_candidates(text)]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return _normalize_parsed(json.loads(candidate))
        except json.JSONDecodeError:
            repaired = _try_repair_json(candidate)
            if repaired is not None:
                logger.info("[Planner] JSON repair succeeded")
                return _normalize_parsed(repaired)

    # 1) Direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return {"done": False, "tasks": parsed, "parse_error": None}
        if isinstance(parsed, dict):
            parsed.setdefault("parse_error", None)
        return parsed
    except json.JSONDecodeError as e:
        logger.warning("[Planner] JSON parse error: %s — attempting repair", e)
        repaired = _try_repair_json(text)
        if repaired is not None:
            logger.info("[Planner] JSON repair succeeded")
            if isinstance(repaired, list):
                return {"done": False, "tasks": repaired, "parse_error": None}
            if isinstance(repaired, dict):
                repaired.setdefault("parse_error", None)
            return repaired
        logger.error("[Planner] JSON parse error: %s\nRaw output:\n%s", e, raw)
        return {"done": False, "tasks": [], "parse_error": str(e)}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    payload = {
        "type": "workflow_stage_changed",
        "run_id": run_id,
        **_build_workflow_stage_update(stage, detail),
    }
    writer(payload)


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def _resolve_run_id(task_pool: list[TaskStatus], current_run_id: str | None) -> str:
    if current_run_id:
        return current_run_id
    for task in task_pool:
        existing = task.get("run_id")
        if existing:
            return existing
    return _new_run_id()


def _normalize_task_pool(task_pool: list[TaskStatus], run_id: str) -> tuple[list[TaskStatus], bool]:
    if not task_pool:
        return task_pool, False

    now = _utc_now_iso()
    normalized: list[TaskStatus] = []
    changed = False
    for task in task_pool:
        normalized_task = dict(task)
        if normalized_task.get("run_id") != run_id:
            normalized_task["run_id"] = run_id
            changed = True
        if not normalized_task.get("updated_at"):
            normalized_task["updated_at"] = now
            changed = True
        normalized.append(TaskStatus(**normalized_task))
    return normalized, changed


def _make_tasks(raw_tasks: list[dict], run_id: str) -> list[TaskStatus]:
    tasks: list[TaskStatus] = []
    for t in raw_tasks:
        desc = (t.get("description") or "").strip()
        if not desc:
            continue
        now = _utc_now_iso()
        tasks.append(
            TaskStatus(
                task_id=str(uuid.uuid4())[:8],
                description=desc,
                run_id=run_id,
                assigned_agent=t.get("assigned_agent") or None,
                status="PENDING",
                status_detail="已规划，等待分派",
                clarification_prompt=None,
                clarification_request=None,
                updated_at=now,
                result=None,
                error=None,
            )
        )
    return tasks

def _normalize_text_value(value: Any) -> str:
    if value is None:
        return ""
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if text.lower() in _NULLISH_TEXT_VALUES:
        return ""
    return text


def _planner_invocation_error_message(exc: Exception) -> str:
    detail = str(exc).strip()
    if detail:
        return f"Workflow planning failed: {detail}"
    return "Workflow planning failed before tasks could be generated."


def _pick_first_non_empty(*values: Any) -> str | None:
    for value in values:
        normalized = _normalize_text_value(value)
        if normalized:
            return normalized
    return None


def _describe_active_stage(task_pool: list[TaskStatus]) -> tuple[WorkflowStage, str | None] | None:
    running_task = next((task for task in task_pool if task["status"] == "RUNNING"), None)
    if running_task is not None:
        return (
            "executing",
            _pick_first_non_empty(
                running_task.get("status_detail"),
                running_task.get("description"),
            ),
        )

    intervention_task = next(
        (task for task in task_pool if task["status"] == "WAITING_INTERVENTION"),
        None,
    )
    if intervention_task is not None:
        intervention_request = intervention_task.get("intervention_request") or {}
        return (
            "executing",
            _pick_first_non_empty(
                intervention_request.get("title") if isinstance(intervention_request, dict) else None,
                intervention_task.get("status_detail"),
                intervention_task.get("description"),
            ),
        )

    waiting_task = next(
        (task for task in task_pool if task["status"] == "WAITING_DEPENDENCY"),
        None,
    )
    if waiting_task is not None:
        return (
            "executing",
            _pick_first_non_empty(
                waiting_task.get("blocked_reason"),
                waiting_task.get("status_detail"),
                waiting_task.get("description"),
            ),
        )

    pending_task = next((task for task in task_pool if task["status"] == "PENDING"), None)
    if pending_task is not None:
        return (
            "routing",
            _pick_first_non_empty(
                pending_task.get("status_detail"),
                pending_task.get("description"),
            ),
        )

    return None


def _build_summarizing_detail(task_pool: list[TaskStatus], planner_goal: str) -> str | None:
    completed_task = next(
        (task for task in reversed(task_pool) if task.get("status") == "DONE"),
        None,
    )
    if completed_task is None:
        return _normalize_text_value(planner_goal) or None
    return _pick_first_non_empty(
        completed_task.get("result"),
        completed_task.get("status_detail"),
        completed_task.get("description"),
        planner_goal,
    )


def _build_queued_detail(original_input: str, planner_goal: str) -> str | None:
    return _pick_first_non_empty(
        planner_goal,
        original_input,
    )


def _extract_original_input(state: ThreadState) -> str:
    messages = state.get("messages") or []
    for msg in messages:
        if is_human_message(msg):
            return _content_to_text(getattr(msg, "content", ""))
    return ""


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


async def planner_node(state: ThreadState, config: RunnableConfig) -> dict:
    domain_agents = list_domain_agents()
    agent_descriptions = _build_agent_descriptions(domain_agents)

    task_pool: list[TaskStatus] = state.get("task_pool") or []
    stored_original_input = state.get("original_input")
    current_run_id = state.get("run_id")
    latest_user_input = extract_latest_user_input(state)
    is_clarification_answer = latest_user_message_is_clarification_answer(state)
    run_id = _resolve_run_id(task_pool, current_run_id)
    writer = _get_event_writer()
    normalized_task_pool, task_pool_changed = _normalize_task_pool(task_pool, run_id)
    task_pool = normalized_task_pool
    pending = [t for t in task_pool if t["status"] == "PENDING"]
    running = [t for t in task_pool if t["status"] == "RUNNING"]
    waiting = [t for t in task_pool if t["status"] == "WAITING_DEPENDENCY"]
    waiting_intervention = [t for t in task_pool if t["status"] == "WAITING_INTERVENTION"]

    if stored_original_input and latest_user_input and latest_user_input != stored_original_input and not is_clarification_answer:
        next_run_id = current_run_id if state.get("workflow_stage") == "acknowledged" and current_run_id else _new_run_id()
        queued_detail = _build_queued_detail(latest_user_input, latest_user_input)
        logger.info("[Planner] New user turn detected, resetting task pool for fresh decomposition.")
        _emit_workflow_stage(writer, "queued", queued_detail, run_id=next_run_id)
        return {
            "task_pool": [],
            "verified_facts": {},
            "original_input": latest_user_input,
            "run_id": next_run_id,
            "planner_goal": latest_user_input,
            "route_count": 0,
            "final_result": None,
            "execution_state": "QUEUED",
            **_build_workflow_stage_update("queued", queued_detail),
        }

    if pending or running or waiting or waiting_intervention:
        logger.info(
            "[Planner] Active tasks detected (pending=%d, running=%d, waiting=%d, waiting_intervention=%d), resuming. "
            "run_id=%s current_run_id=%s latest_user_input=%r clarification_resume=%s task_summary=%s",
            len(pending),
            len(running),
            len(waiting),
            len(waiting_intervention),
            run_id,
            current_run_id,
            latest_user_input,
            is_clarification_answer,
            _summarize_tasks_for_log(task_pool),
        )
        result = {"execution_state": "RESUMING", "run_id": run_id}
        active_stage = _describe_active_stage(task_pool)
        if active_stage is not None:
            result.update(_build_workflow_stage_update(*active_stage))
        if task_pool_changed or current_run_id != run_id:
            result["task_pool"] = task_pool
        return result

    original_input: str = stored_original_input or latest_user_input or _extract_original_input(state)
    planner_goal: str = state.get("planner_goal") or original_input
    is_first_run = not task_pool

    if is_first_run and state.get("workflow_stage") == "acknowledged":
        queued_detail = _build_queued_detail(original_input, planner_goal)
        logger.info("[Planner] Workflow acknowledged; emitting queued stage before planning.")
        _emit_workflow_stage(writer, "queued", queued_detail, run_id=run_id)
        result = {
            "execution_state": "QUEUED",
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            "route_count": state.get("route_count") or 0,
            **_build_workflow_stage_update("queued", queued_detail),
        }
        if task_pool_changed and task_pool:
            result["task_pool"] = task_pool
        return result

    if is_first_run:
        system_prompt = DECOMPOSE_SYSTEM_PROMPT.format(agent_descriptions=agent_descriptions)
        user_message = original_input
        logger.info("[Planner] Mode=decompose, input=%r", original_input[:100])
        stage_name: WorkflowStage = "planning"
        stage_detail = _pick_first_non_empty(original_input, planner_goal)
    else:
        tasks_summary = _build_tasks_summary(task_pool)
        facts_summary = _build_facts_summary(state.get("verified_facts") or {})
        system_prompt = VALIDATE_SYSTEM_PROMPT.format(
            original_input=planner_goal,
            tasks_summary=tasks_summary,
            facts_summary=facts_summary,
            agent_descriptions=agent_descriptions,
        )
        user_message = "Please evaluate whether the goal has been achieved."
        logger.info("[Planner] Mode=validate, %d tasks done", len(task_pool))
        logger.info("[Planner] Validate task summary: %s", tasks_summary[:2000])
        logger.info("[Planner] Validate facts summary: %s", facts_summary[:2000])
        stage_name = "summarizing"
        stage_detail = _build_summarizing_detail(task_pool, planner_goal)

    _emit_workflow_stage(writer, stage_name, stage_detail, run_id=run_id)

    llm = create_chat_model(name=_resolve_model(config), thinking_enabled=False)
    try:
        response = await llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
    except Exception as exc:
        error_message = _planner_invocation_error_message(exc)
        logger.exception("[Planner] Model invocation failed: %s", error_message)
        _emit_workflow_stage(writer, stage_name, error_message, run_id=run_id)
        return {
            "execution_state": "ERROR",
            "final_result": error_message,
            "messages": [AIMessage(content=error_message)],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(stage_name, error_message),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    raw_response_text = _content_to_text(response.content)
    logger.info("[Planner] Raw model output: %s", raw_response_text[:2000])
    parsed = _parse_planner_output(response.content)

    if parsed.get("parse_error"):
        error_message = "规划器输出格式异常，暂时无法继续执行。"
        logger.error("[Planner] %s Raw output=%r", error_message, response.content[:500])
        _emit_workflow_stage(writer, stage_name, error_message, run_id=run_id)
        return {
            "execution_state": "ERROR",
            "final_result": error_message,
            "messages": [AIMessage(content=error_message)],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(stage_name, error_message),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    if parsed.get("done"):
        summary = _normalize_text_value(parsed.get("summary", ""))
        terminal_detail = _pick_first_non_empty(
            summary,
            stage_detail,
            "任务已完成。",
        )
        logger.info("[Planner] Goal achieved. Summary length=%d", len(summary))
        _emit_workflow_stage(writer, "summarizing", terminal_detail, run_id=run_id)
        return {
            "execution_state": "DONE",
            "final_result": summary,
            "messages": [AIMessage(content=summary or "任务已完成。")],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update("summarizing", terminal_detail),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    logger.info("[Planner] Parsed task payload: %s", json.dumps(parsed.get("tasks", []), ensure_ascii=False)[:2000])
    new_tasks = _make_tasks(parsed.get("tasks", []), run_id=run_id)
    if not new_tasks:
        logger.error("[Planner] No tasks generated for unfinished goal; stopping to avoid false completion.")
        fallback = "Planner produced no actionable tasks for an unfinished goal."
        _emit_workflow_stage(writer, stage_name, fallback, run_id=run_id)
        return {
            "execution_state": "ERROR",
            "final_result": fallback,
            "messages": [AIMessage(content=fallback)],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(stage_name, fallback),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    completed_tasks = [task for task in task_pool if task.get("status") == "DONE"]
    if completed_tasks:
        last_completed_task = completed_tasks[-1]
        logger.warning(
            "[Planner] Validation rejected prior completion and generated follow-up work. "
            "run_id=%s goal=%r completed_count=%d follow_up_count=%d last_completed_task=%s last_completed_result=%r "
            "completed=%s follow_ups=%s",
            run_id,
            planner_goal[:200],
            len(completed_tasks),
            len(new_tasks),
            f"{last_completed_task['task_id']}:{last_completed_task.get('assigned_agent') or '?'}:{last_completed_task['description'][:120]}",
            (last_completed_task.get("result") or "")[:300],
            _summarize_tasks_for_log(completed_tasks),
            _summarize_tasks_for_log(new_tasks),
        )

    logger.info("[Planner] Generated %d task(s): %s", len(new_tasks), _summarize_tasks_for_log(new_tasks))
    _emit_workflow_stage(
        writer,
        "routing",
        _pick_first_non_empty(new_tasks[0].get("description"), planner_goal),
        run_id=run_id,
    )
    return {
        "task_pool": new_tasks,
        "execution_state": "PLANNING_DONE",
        "original_input": original_input,
        "run_id": run_id,
        "planner_goal": planner_goal,
        "route_count": 0,
        **_build_workflow_stage_update(
            "routing",
            _pick_first_non_empty(new_tasks[0].get("description"), planner_goal),
        ),
    }
