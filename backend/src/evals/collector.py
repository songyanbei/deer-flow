"""Metrics collector for benchmark runs.

Extracts evaluation metrics from the real ThreadState produced by the
compiled workflow graph (planner -> router -> executor).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


def collect_metrics(state: dict[str, Any], *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Collect evaluation metrics from the final ThreadState.

    Parameters
    ----------
    state : dict
        The final state dict returned by ``graph.ainvoke()``.
    events : list, optional
        Custom events captured during graph execution via patched stream writers.
    """
    events = events or []
    task_pool = state.get("task_pool") or []
    verified_facts = state.get("verified_facts") or {}
    messages = state.get("messages") or []

    # Assigned agents: deduplicated, order-preserving
    assigned_agents: list[str] = []
    seen_agents: set[str] = set()
    for task in task_pool:
        agent = task.get("assigned_agent")
        if agent and agent not in seen_agents:
            assigned_agents.append(agent)
            seen_agents.add(agent)

    # Count clarifications and interventions from multiple sources
    clarification_count = 0
    intervention_count = 0

    # 1. Check messages for clarification/intervention signals
    for msg in messages:
        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "")
            if name == "ask_clarification":
                clarification_count += 1
            elif name == "intervention_required":
                intervention_count += 1
            elif name == "task_fail":
                content = getattr(msg, "content", "")
                if "rejected" in content.lower() or "拒绝" in content:
                    intervention_count += 1
        elif isinstance(msg, AIMessage):
            name = getattr(msg, "name", "")
            if name == "ask_clarification":
                clarification_count += 1

    # 2. Check task pool for clarification/intervention status
    for task in task_pool:
        status_detail = task.get("status_detail", "")
        pending_interrupt = task.get("pending_interrupt") or {}
        interrupt_type = pending_interrupt.get("interrupt_type", "")
        continuation_mode = task.get("continuation_mode", "")
        intervention_status = task.get("intervention_status", "")

        # Clarification detected from task state
        if clarification_count == 0:
            if (status_detail == "@waiting_clarification"
                    or interrupt_type == "clarification"
                    or continuation_mode == "continue_after_clarification"):
                clarification_count += 1

        # Intervention detected from task state
        if intervention_count == 0:
            if (interrupt_type == "intervention"
                    or intervention_status in ("resolved", "consumed", "rejected", "pending")
                    or continuation_mode == "continue_after_intervention"):
                intervention_count += 1

        # Task failed due to rejected intervention
        if intervention_count == 0:
            if task.get("status") == "FAILED":
                error = task.get("error", "")
                if error and ("rejected" in error.lower() or "拒绝" in error):
                    intervention_count += 1

    metrics: dict[str, Any] = {
        "task_count": len(task_pool),
        "route_count": state.get("route_count", 0),
        "clarification_count": clarification_count,
        "intervention_count": intervention_count,
        "verified_fact_count": len(verified_facts),
        "assigned_agents": assigned_agents,
        "event_count": len(events),
        "stage_transitions": _extract_stage_transitions(events),
        "llm_metrics": {
            "total_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        },
    }

    return metrics


def _extract_stage_transitions(events: list[dict[str, Any]]) -> list[str]:
    """Extract the sequence of workflow stage transitions from events."""
    return [
        e.get("stage", e.get("workflow_stage", ""))
        for e in events
        if isinstance(e, dict) and e.get("type") == "workflow_stage_changed"
    ]
