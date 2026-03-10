from __future__ import annotations

import re
from typing import Literal, TypedDict

from langchain_core.runnables import RunnableConfig

from src.agents.thread_state import RequestedOrchestrationMode, ResolvedOrchestrationMode, ThreadState
from src.config.agents_config import load_agent_config


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
    "并行",
    "分别",
    "多步骤",
    "调研",
    "报告",
    "汇总",
    "总结",
    "规划",
    "步骤",
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
    "探索",
    "看看",
    "怎么",
    "为什么",
    "代码",
    "文件",
    "网页",
    "搜索",
    "快速",
)


def _normalize_requested_mode(value: object) -> RequestedOrchestrationMode:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _VALID_REQUESTED:
            return lowered  # type: ignore[return-value]
    return "auto"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _is_human_message(message: object) -> bool:
    return getattr(message, "type", None) == "human" or message.__class__.__name__ == "HumanMessage"


def _extract_latest_user_input(state: ThreadState) -> str:
    for message in reversed(state.get("messages") or []):
        if _is_human_message(message):
            return _content_to_text(getattr(message, "content", ""))
    return ""


def _latest_user_message_is_clarification_answer(state: ThreadState) -> bool:
    messages = state.get("messages") or []
    if len(messages) < 2:
        return False

    last = messages[-1]
    prev = messages[-2]
    if not _is_human_message(last):
        return False

    prev_name = getattr(prev, "name", None)
    prev_content = _content_to_text(getattr(prev, "content", ""))
    return prev_name == "ask_clarification" or "clarification" in prev_content.lower() or "澄清" in prev_content


def _count_matches(text: str, patterns: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for pattern in patterns if pattern in lowered or pattern in text)


def _looks_like_multiple_goals(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"(^|\n)\s*(\d+\.|-|\*)\s+", text):
        return True
    connectors = (
        " and ",
        " then ",
        " also ",
        "同时",
        "并且",
        "以及",
        "分别",
        "然后",
    )
    return sum(1 for connector in connectors if connector in lowered or connector in text) >= 1


def _load_agent_default_mode(config: RunnableConfig) -> RequestedOrchestrationMode | None:
    agent_name = config.get("configurable", {}).get("agent_name")
    if not isinstance(agent_name, str) or not agent_name:
        return None
    try:
        agent_config = load_agent_config(agent_name)
    except Exception:
        return None
    if agent_config.requested_orchestration_mode in _VALID_REQUESTED:
        return agent_config.requested_orchestration_mode
    return None


def decide_orchestration(state: ThreadState, config: RunnableConfig) -> OrchestrationDecision:
    configurable = config.get("configurable", {})
    requested_mode = _normalize_requested_mode(
        configurable.get("requested_orchestration_mode") or configurable.get("orchestration_mode")
    )
    existing_requested_mode = _normalize_requested_mode(state.get("requested_orchestration_mode"))
    existing_resolved_mode = state.get("resolved_orchestration_mode")

    if _latest_user_message_is_clarification_answer(state) and existing_resolved_mode in {"leader", "workflow"}:
        return {
            "requested_mode": existing_requested_mode,
            "resolved_mode": existing_resolved_mode,
            "reason": f"Resume current {existing_resolved_mode} run after clarification",
            "workflow_score": 0,
            "leader_score": 0,
        }

    if requested_mode in {"leader", "workflow"}:
        return {
            "requested_mode": requested_mode,
            "resolved_mode": requested_mode,
            "reason": f"User explicitly requested {requested_mode}",
            "workflow_score": 0,
            "leader_score": 0,
        }

    agent_default_mode = _load_agent_default_mode(config)
    if agent_default_mode in {"leader", "workflow"}:
        return {
            "requested_mode": requested_mode,
            "resolved_mode": agent_default_mode,
            "reason": f"Agent default routed to {agent_default_mode}",
            "workflow_score": 0,
            "leader_score": 0,
        }

    latest_input = _extract_latest_user_input(state)
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
            "reason": "Detected structured or multi-step task; routed to workflow",
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


def orchestration_selector_node(state: ThreadState, config: RunnableConfig) -> dict:
    decision = decide_orchestration(state, config)
    return {
        "requested_orchestration_mode": decision["requested_mode"],
        "resolved_orchestration_mode": decision["resolved_mode"],
        "orchestration_reason": decision["reason"],
    }
