import hashlib
import json
from typing import Any


def generate_tool_semantic_fingerprint(
    agent_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """Build a deterministic fingerprint for a tool intervention."""
    normalized_args = json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)
    raw = f"{agent_name}:{tool_name}:{normalized_args}"
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
