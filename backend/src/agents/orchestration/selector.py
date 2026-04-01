from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from src.agents.thread_state import (
    RequestedOrchestrationMode,
    ResolvedOrchestrationMode,
    ThreadState,
    WorkflowStage,
)
from src.agents.workflow_resume import (
    extract_latest_user_input,
    latest_user_message_is_clarification_answer,
)
from src.config.agents_config import load_agent_config
from src.config.paths import resolve_tenant_agents_dir
from src.observability import record_decision

logger = logging.getLogger(__name__)


class OrchestrationDecision(TypedDict):
    requested_mode: RequestedOrchestrationMode
    resolved_mode: ResolvedOrchestrationMode
    reason: str
    workflow_score: int
    leader_score: int


_VALID_REQUESTED = {"auto", "leader", "workflow"}
_WORKFLOW_HINTS = (
    "workflow",
    "report",
    "research",
    "plan",
    "steps",
    "step by step",
    "compare",
    "validate",
    "summarize",
    "cross-check",
    "\u5e76\u884c",
    "\u5206\u522b",
    "\u591a\u6b65",
    "\u8c03\u7814",
    "\u62a5\u544a",
    "\u6c47\u603b",
    "\u603b\u7ed3",
    "\u89c4\u5212",
    "\u6b65\u9aa4",
)
_LEADER_HINTS = (
    "explore",
    "brainstorm",
    "how to",
    "what is",
    "why",
    "quick",
    "search",
    "browse",
    "code",
    "file",
    "web",
    "\u63a2\u7d22",
    "\u770b\u770b",
    "\u600e\u4e48",
    "\u4e3a\u4ec0\u4e48",
    "\u4ee3\u7801",
    "\u6587\u4ef6",
    "\u7f51\u9875",
    "\u641c\u7d22",
    "\u5feb\u901f",
)
_MULTI_GOAL_CONNECTORS = (
    " and ",
    " then ",
    " also ",
    "\u540c\u65f6",
    "\u5e76\u4e14",
    "\u4ee5\u53ca",
    "\u5206\u522b",
    "\u7136\u540e",
)


def _normalize_requested_mode(value: object) -> RequestedOrchestrationMode:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _VALID_REQUESTED:
            return lowered  # type: ignore[return-value]
    return "auto"


def _orchestration_payload(config: RunnableConfig) -> dict:
    configurable = config.get("configurable", {})
    if isinstance(configurable, dict) and configurable:
        return configurable
    context = config.get("context", {})
    if isinstance(context, dict):
        return context
    return {}


def _workflow_clarification_resume_requested(config: RunnableConfig) -> bool:
    payload = _orchestration_payload(config)
    return bool(payload.get("workflow_clarification_resume"))

def _count_matches(text: str, patterns: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for pattern in patterns if pattern in lowered or pattern in text)


def _looks_like_multiple_goals(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"(^|\n)\s*(\d+\.|-|\*)\s+", text):
        return True
    return sum(
        1
        for connector in _MULTI_GOAL_CONNECTORS
        if connector in lowered or connector in text
    ) >= 1


def _load_agent_default_mode(
    config: RunnableConfig,
) -> RequestedOrchestrationMode | None:
    agent_name = config.get("configurable", {}).get("agent_name")
    if not isinstance(agent_name, str) or not agent_name:
        return None
    tenant_id = config.get("configurable", {}).get("tenant_id", "default")
    agents_dir = resolve_tenant_agents_dir(tenant_id)
    try:
        agent_config = load_agent_config(agent_name, agents_dir=agents_dir)
    except Exception:
        return None
    if agent_config.requested_orchestration_mode in _VALID_REQUESTED:
        return agent_config.requested_orchestration_mode
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


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


def decide_orchestration(
    state: ThreadState,
    config: RunnableConfig,
) -> OrchestrationDecision:
    configurable = _orchestration_payload(config)
    requested_mode = _normalize_requested_mode(
        configurable.get("requested_orchestration_mode")
        or configurable.get("orchestration_mode")
    )
    existing_requested_mode = _normalize_requested_mode(
        state.get("requested_orchestration_mode")
    )
    existing_resolved_mode = state.get("resolved_orchestration_mode")

    if (
        _workflow_clarification_resume_requested(config)
        and existing_resolved_mode in {"leader", "workflow"}
    ):
        return {
            "requested_mode": existing_requested_mode,
            "resolved_mode": existing_resolved_mode,
            "reason": f"澄清完成后，继续当前{existing_resolved_mode}流程",
            "workflow_score": 0,
            "leader_score": 0,
        }

    if (
        latest_user_message_is_clarification_answer(state)
        and existing_resolved_mode in {"leader", "workflow"}
    ):
        return {
            "requested_mode": existing_requested_mode,
            "resolved_mode": existing_resolved_mode,
            "reason": f"已收到补充信息，继续当前 {existing_resolved_mode} 流程",
            "workflow_score": 0,
            "leader_score": 0,
        }

    if requested_mode in {"leader", "workflow"}:
        return {
            "requested_mode": requested_mode,
            "resolved_mode": requested_mode,
            "reason": f"用户明确选择了 {requested_mode} 模式",
            "workflow_score": 0,
            "leader_score": 0,
        }

    agent_default_mode = _load_agent_default_mode(config)
    if agent_default_mode in {"leader", "workflow"}:
        return {
            "requested_mode": requested_mode,
            "resolved_mode": agent_default_mode,
            "reason": f"按智能体默认配置选择 {agent_default_mode} 模式",
            "workflow_score": 0,
            "leader_score": 0,
        }

    latest_input = extract_latest_user_input(state)
    workflow_score = 0
    leader_score = 0

    if _looks_like_multiple_goals(latest_input):
        workflow_score += 2
    workflow_score += min(_count_matches(latest_input, _WORKFLOW_HINTS), 2)

    if len(latest_input.split()) <= 18:
        leader_score += 1
    if not _looks_like_multiple_goals(latest_input):
        leader_score += 1
    leader_score += min(_count_matches(latest_input, _LEADER_HINTS), 2)

    if workflow_score >= 3 and workflow_score > leader_score:
        return {
            "requested_mode": requested_mode,
            "resolved_mode": "workflow",
            "reason": "检测到多步骤任务，已切换到 workflow 模式",
            "workflow_score": workflow_score,
            "leader_score": leader_score,
        }

    return {
        "requested_mode": requested_mode,
        "resolved_mode": "leader",
        "reason": "Defaulted to leader for open-ended or low-structure input",
        "workflow_score": workflow_score,
        "leader_score": leader_score,
    }


def _resolve_workflow_run_id(
    state: ThreadState,
    decision: OrchestrationDecision,
    config: RunnableConfig,
) -> str | None:
    if decision["resolved_mode"] != "workflow":
        return None

    existing_run_id = state.get("run_id")
    if (
        existing_run_id
        and _workflow_clarification_resume_requested(config)
        and state.get("resolved_orchestration_mode") == "workflow"
    ):
        return existing_run_id

    if (
        existing_run_id
        and latest_user_message_is_clarification_answer(state)
        and state.get("resolved_orchestration_mode") == "workflow"
    ):
        return existing_run_id

    if (
        existing_run_id
        and state.get("resolved_orchestration_mode") == "workflow"
        and state.get("workflow_stage") == "queued"
        and state.get("execution_state") == "QUEUED"
    ):
        return existing_run_id

    return _new_run_id()


def _has_authoritative_workflow_stage(
    state: ThreadState,
    workflow_run_id: str | None,
) -> bool:
    if not workflow_run_id:
        return False
    if state.get("run_id") != workflow_run_id:
        return False
    stage = state.get("workflow_stage")
    return stage in {"queued", "planning", "routing", "executing", "summarizing"}


def orchestration_selector_node(state: ThreadState, config: RunnableConfig) -> dict:
    decision = decide_orchestration(state, config)
    workflow_run_id = _resolve_workflow_run_id(state, decision, config)
    preserve_existing_stage = (
        decision["resolved_mode"] == "workflow"
        and _has_authoritative_workflow_stage(state, workflow_run_id)
    )
    logger.info(
        "[Selector] resolved_mode=%s requested_mode=%s run_id=%s existing_run_id=%s "
        "clarification_resume_flag=%s latest_resume_like=%s execution_state=%s task_statuses=%s",
        decision["resolved_mode"],
        decision["requested_mode"],
        workflow_run_id,
        state.get("run_id"),
        _workflow_clarification_resume_requested(config),
        latest_user_message_is_clarification_answer(state),
        state.get("execution_state"),
        [task.get("status") for task in (state.get("task_pool") or []) if isinstance(task, dict)],
    )
    record_decision(
        "orchestration_mode",
        run_id=workflow_run_id,
        inputs={
            "requested_mode": decision["requested_mode"],
            "workflow_score": decision["workflow_score"],
            "leader_score": decision["leader_score"],
        },
        output={"resolved_mode": decision["resolved_mode"]},
        reason=decision["reason"],
        alternatives=[("workflow" if decision["resolved_mode"] == "leader" else "leader")],
    )
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    if writer is not None:
        writer(
            {
                "type": "orchestration_mode_resolved",
                "requested_orchestration_mode": decision["requested_mode"],
                "resolved_orchestration_mode": decision["resolved_mode"],
                "orchestration_reason": decision["reason"],
                "run_id": workflow_run_id,
            }
        )
        if decision["resolved_mode"] == "workflow" and not preserve_existing_stage:
            _emit_workflow_stage(
                writer,
                "acknowledged",
                decision["reason"],
                run_id=workflow_run_id,
            )

    result = {
        "requested_orchestration_mode": decision["requested_mode"],
        "resolved_orchestration_mode": decision["resolved_mode"],
        "orchestration_reason": decision["reason"],
    }
    if decision["resolved_mode"] == "workflow":
        result["run_id"] = workflow_run_id
        if not preserve_existing_stage:
            result.update(_build_workflow_stage_update("acknowledged", decision["reason"]))
    else:
        result.update(_build_workflow_stage_update(None))
    return result
