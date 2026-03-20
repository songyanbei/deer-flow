"""Structured decision log — outputs JSON Lines to a dedicated logger."""

import json
import logging
import time
from typing import Any, Literal

DecisionType = Literal[
    "orchestration_mode",
    "task_decomposition",
    "workflow_completion",
    "agent_route",
    "agent_route_fallback",
    "helper_dispatch",
    "helper_retry",
    "budget_escalation",
    "dependency_resolution",
    "outcome_classification",
    "intervention_trigger",
    "intervention_resolution",
]

_decision_logger = logging.getLogger("deer-flow.decisions")


def _truncate_dict(obj: Any, max_str_len: int = 500) -> Any:
    """Recursively truncate string values in dicts/lists."""
    if isinstance(obj, str):
        return obj[:max_str_len] + "..." if len(obj) > max_str_len else obj
    if isinstance(obj, dict):
        return {k: _truncate_dict(v, max_str_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_dict(item, max_str_len) for item in obj]
    return obj


def record_decision(
    decision_type: DecisionType,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    agent_name: str | None = None,
    inputs: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    reason: str | None = None,
    alternatives: list[str] | None = None,
    confidence: float | None = None,
    duration_ms: float | None = None,
) -> None:
    """Record a structured decision to the decisions logger.

    Builds a dict, strips None values, serializes to JSON, and emits
    via the ``deer-flow.decisions`` logger at INFO level.
    """
    entry: dict[str, Any] = {
        "ts": time.time(),
        "decision_type": decision_type,
    }

    optional_fields: dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": agent_name,
        "inputs": _truncate_dict(inputs) if inputs else None,
        "output": _truncate_dict(output) if output else None,
        "reason": reason,
        "alternatives": alternatives,
        "confidence": confidence,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
    }

    for key, value in optional_fields.items():
        if value is not None:
            entry[key] = value

    try:
        json_str = json.dumps(entry, ensure_ascii=False, default=str)
    except Exception:
        json_str = json.dumps({"decision_type": decision_type, "error": "serialization_failed"})

    _decision_logger.info(json_str)
