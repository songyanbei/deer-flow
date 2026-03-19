import hashlib
import json
from typing import Any


def _normalize_tool_args(tool_args: dict[str, Any]) -> str:
    return json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)


def generate_tool_interrupt_fingerprint(
    run_id: str,
    task_id: str,
    agent_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """Build a deterministic per-interrupt fingerprint for a tool intervention."""
    raw = f"{run_id}:{task_id}:{agent_name}:{tool_name}:{_normalize_tool_args(tool_args)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def generate_tool_semantic_fingerprint(
    agent_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """Build a deterministic fingerprint for a tool intervention."""
    raw = f"{agent_name}:{tool_name}:{_normalize_tool_args(tool_args)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def generate_tool_snapshot_hash(
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """Build a deterministic hash for the pending tool payload being resumed."""
    raw = f"{tool_name}:{_normalize_tool_args(tool_args)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def generate_clarification_semantic_fingerprint(
    agent_name: str,
    question: str,
    options: list[str],
) -> str:
    """Build a deterministic fingerprint for a clarification intervention."""
    normalized_options = json.dumps(sorted(options), ensure_ascii=False) if options else ""
    raw = f"{agent_name}:{question.strip()}:{normalized_options}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
