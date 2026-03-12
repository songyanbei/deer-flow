"""Planner node: task decomposition and goal validation for the multi-agent graph."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.planner.prompt import DECOMPOSE_SYSTEM_PROMPT, VALIDATE_SYSTEM_PROMPT
from src.agents.thread_state import TaskStatus, ThreadState, VerifiedFact, WorkflowStage
from src.config.agents_config import list_domain_agents
from src.models import create_chat_model

logger = logging.getLogger(__name__)
_NULLISH_TEXT_VALUES = {"none", "null", "undefined"}


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
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return {"done": False, "tasks": parsed, "parse_error": None}
        if isinstance(parsed, dict):
            parsed.setdefault("parse_error", None)
        return parsed
    except json.JSONDecodeError as e:
        logger.error("[Planner] JSON parse error: %s\nRaw output:\n%s", e, raw)
        return {"done": False, "tasks": [], "parse_error": str(e)}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                status_detail="Planned and waiting for routing",
                clarification_prompt=None,
                updated_at=now,
                result=None,
                error=None,
            )
        )
    return tasks


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


def _is_human_message(message) -> bool:
    return getattr(message, "type", None) == "human" or message.__class__.__name__ == "HumanMessage"


def _extract_original_input(state: ThreadState) -> str:
    messages = state.get("messages") or []
    for msg in messages:
        if _is_human_message(msg):
            return _content_to_text(getattr(msg, "content", ""))
    return ""


def _extract_latest_user_input(state: ThreadState) -> str:
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if _is_human_message(msg):
            return _content_to_text(getattr(msg, "content", ""))
    return ""


def _latest_user_message_is_clarification_answer(state: ThreadState) -> bool:
    messages = state.get("messages") or []
    if len(messages) < 2:
        return False
    last = messages[-1]
    prev = messages[-2]
    if not _is_human_message(last):
        return False
    return getattr(prev, "type", None) == "ai" and getattr(prev, "name", None) == "ask_clarification"


async def planner_node(state: ThreadState, config: RunnableConfig) -> dict:
    domain_agents = list_domain_agents()
    agent_descriptions = _build_agent_descriptions(domain_agents)

    task_pool: list[TaskStatus] = state.get("task_pool") or []
    stored_original_input = state.get("original_input")
    current_run_id = state.get("run_id")
    latest_user_input = _extract_latest_user_input(state)
    is_clarification_answer = _latest_user_message_is_clarification_answer(state)
    run_id = _resolve_run_id(task_pool, current_run_id)
    writer = _get_event_writer()
    normalized_task_pool, task_pool_changed = _normalize_task_pool(task_pool, run_id)
    task_pool = normalized_task_pool
    pending = [t for t in task_pool if t["status"] == "PENDING"]
    running = [t for t in task_pool if t["status"] == "RUNNING"]
    waiting = [t for t in task_pool if t["status"] == "WAITING_DEPENDENCY"]

    if stored_original_input and latest_user_input and latest_user_input != stored_original_input and not is_clarification_answer:
        next_run_id = _new_run_id()
        logger.info("[Planner] New user turn detected, resetting task pool for fresh decomposition.")
        return {
            "task_pool": [],
            "verified_facts": {},
            "original_input": latest_user_input,
            "run_id": next_run_id,
            "planner_goal": latest_user_input,
            "route_count": 0,
            "final_result": None,
            "execution_state": "PLANNING_RESET",
            **_build_workflow_stage_update("planning", latest_user_input),
        }

    if pending or running or waiting:
        logger.info(
            "[Planner] Active tasks detected (pending=%d, running=%d, waiting=%d), resuming.",
            len(pending),
            len(running),
            len(waiting),
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
        return {
            "execution_state": "ERROR",
            "final_result": error_message,
            "messages": [AIMessage(content=error_message)],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(None),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    raw_response_text = _content_to_text(response.content)
    logger.info("[Planner] Raw model output: %s", raw_response_text[:2000])
    parsed = _parse_planner_output(response.content)

    if parsed.get("parse_error"):
        error_message = "Planner failed to produce valid structured output."
        logger.error("[Planner] %s Raw output=%r", error_message, response.content[:500])
        return {
            "execution_state": "ERROR",
            "final_result": error_message,
            "messages": [AIMessage(content=error_message)],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(None),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    if parsed.get("done"):
        summary = _normalize_text_value(parsed.get("summary", ""))
        logger.info("[Planner] Goal achieved. Summary length=%d", len(summary))
        return {
            "execution_state": "DONE",
            "final_result": summary,
            "messages": [AIMessage(content=summary or "Task completed.")],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(None),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

    logger.info("[Planner] Parsed task payload: %s", json.dumps(parsed.get("tasks", []), ensure_ascii=False)[:2000])
    new_tasks = _make_tasks(parsed.get("tasks", []), run_id=run_id)
    if not new_tasks:
        logger.error("[Planner] No tasks generated for unfinished goal; stopping to avoid false completion.")
        fallback = "Planner produced no actionable tasks for an unfinished goal."
        return {
            "execution_state": "ERROR",
            "final_result": fallback,
            "messages": [AIMessage(content=fallback)],
            "original_input": original_input,
            "run_id": run_id,
            "planner_goal": planner_goal,
            **_build_workflow_stage_update(None),
            **({"task_pool": task_pool} if task_pool_changed and task_pool else {}),
        }

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
