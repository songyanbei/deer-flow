"""Semantic Router node for multi-agent task assignment."""

import logging
import re
from datetime import datetime, timezone
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from src.agents.thread_state import TaskStatus, ThreadState
from src.config.agents_config import list_domain_agents
from src.models import create_chat_model

logger = logging.getLogger(__name__)

MAX_ROUTE_COUNT = 12


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_run_id(state: ThreadState, task: TaskStatus) -> str:
    return state.get("run_id") or task.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"


def _resolve_model(config: RunnableConfig) -> str | None:
    return config.get("configurable", {}).get("model_name") or config.get("configurable", {}).get("model")


def _build_agent_profiles(agents) -> str:
    return "\n".join(f"- {a.name}: {a.description}" for a in agents)


def _fact_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "summary" in value:
            return str(value["summary"])
        if "result" in value:
            return str(value["result"])
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
        output = response.content

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
    pending = [t for t in task_pool if t["status"] == "PENDING"]
    running = [t for t in task_pool if t["status"] == "RUNNING"]

    if running:
        logger.info("[Router] Found RUNNING task, forwarding to executor.")
        return {
            "execution_state": "ROUTING_DONE",
            "route_count": route_count,
        }

    if not pending:
        logger.info("[Router] No pending tasks, signaling planner.")
        return {"execution_state": "PLANNING_NEEDED", "route_count": route_count}

    task = pending[0]
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
    }  # type: ignore[typeddict-item]
    return {
        "task_pool": [updated_task],
        "run_id": run_id,
        "execution_state": "ROUTING_DONE",
        "route_count": route_count,
    }
