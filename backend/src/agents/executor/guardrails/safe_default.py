"""Safe default policy applied when a nudge retry is exhausted.

When the agent still produces unstructured output after a nudge, the safe
default converts the outcome to ``complete`` rather than relying on
fragile heuristic classification.

Rationale: a false-complete (user sees a result with a trailing polite
question) is far less disruptive than a false-interrupt (the main workflow
is blocked waiting for user input that isn't actually needed).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agents.executor.outcome import CompleteOutcome, AgentOutcome

logger = logging.getLogger(__name__)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def apply_safe_default(
    *,
    outcome: AgentOutcome,
    messages: list[Any],
    new_messages_start: int,
) -> CompleteOutcome:
    """Convert any fallback outcome to a ``CompleteOutcome``.

    Extracts the best available text from the original outcome and wraps it
    in a proper ``CompleteOutcome`` with a valid ``fact_payload``.
    """
    agent_output = outcome.get("result_text", "") or outcome.get("prompt", "") or ""

    if not agent_output:
        # Last resort: scan current-round messages for AI text
        from src.agents.executor.outcome import _extract_agent_output
        current_round = messages[new_messages_start:]
        agent_output = _extract_agent_output(current_round) or ""

    fact_payload = _parse_json_object(agent_output)
    if fact_payload is None:
        fact_payload = {"text": agent_output} if agent_output else {}

    logger.info(
        "[SafeDefault] Converting fallback outcome kind=%s to complete. output_len=%d",
        outcome.get("kind", "?"),
        len(agent_output),
    )
    return CompleteOutcome(
        kind="complete",
        messages=messages,
        new_messages_start=new_messages_start,
        result_text=agent_output,
        fact_payload=fact_payload,
    )
