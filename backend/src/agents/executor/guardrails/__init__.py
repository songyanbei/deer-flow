"""Output guardrail gate for domain agent executor.

Public API
----------
``run_output_guardrails``
    Evaluate registered guardrails after ``normalize_agent_outcome``.
    Potentially re-invokes the agent with a nudge message if the guardrail
    recommends a retry.

The guardrail gate sits between outcome normalization and the executor's
branching logic, ensuring that domain agents comply with the structured
output contract (terminal tool calls) before the workflow acts on the
outcome.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.agents.executor.outcome import AgentOutcome, normalize_agent_outcome
from src.agents.thread_state import TaskStatus

from .base import (
    GuardrailContext,
    GuardrailMetadata,
    GuardrailResult,
    GuardrailVerdict,
    OutputGuardrail,
)
from .structured_completion import StructuredCompletionGuardrail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default guardrail registry
# ---------------------------------------------------------------------------

_DEFAULT_GUARDRAILS: list[OutputGuardrail] = [
    StructuredCompletionGuardrail(),
]


def _get_guardrails(enabled: bool = True) -> list[OutputGuardrail]:
    """Return the active guardrail chain, sorted by priority."""
    if not enabled:
        return []
    return sorted(_DEFAULT_GUARDRAILS, key=lambda g: g.priority)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_output_guardrails(
    *,
    task: TaskStatus,
    agent_name: str,
    messages: list[Any],
    new_messages_start: int,
    outcome: AgentOutcome,
    used_fallback: bool,
    agent_config: RunnableConfig,
    make_agent_fn: Callable,
    max_retries: int = 1,
    enabled: bool = True,
) -> tuple[AgentOutcome, bool, GuardrailMetadata]:
    """Evaluate output guardrails and potentially re-invoke the agent.

    Parameters
    ----------
    task:
        The current task status dict.
    agent_name:
        Name of the domain agent.
    messages:
        Full message list returned by the agent invocation.
    new_messages_start:
        Index boundary for current-round messages.
    outcome:
        Result from ``normalize_agent_outcome``.
    used_fallback:
        Whether the classification relied on heuristic fallback.
    agent_config:
        ``RunnableConfig`` for creating a new agent instance.
    make_agent_fn:
        Factory callable (``make_lead_agent``) for building the agent.
    max_retries:
        Maximum number of nudge re-invocations (default 1).
    enabled:
        Master switch.  When ``False``, guardrails are skipped entirely.

    Returns
    -------
    tuple of (final_outcome, final_used_fallback, metadata)
    """
    meta = GuardrailMetadata(
        original_outcome_kind=outcome.get("kind", ""),
        final_outcome_kind=outcome.get("kind", ""),
    )

    guardrails = _get_guardrails(enabled)
    if not guardrails:
        return outcome, used_fallback, meta

    # Evaluate guardrails — first NUDGE_RETRY or OVERRIDE wins
    current_outcome = outcome
    current_fallback = used_fallback
    current_messages = messages
    current_nms = new_messages_start

    for attempt in range(max_retries + 1):
        ctx = GuardrailContext(
            task=task,
            agent_name=agent_name,
            messages=current_messages,
            new_messages_start=current_nms,
            outcome=current_outcome,
            used_fallback=current_fallback,
            attempt=attempt,
            agent_config=agent_config,
            max_retries=max_retries,
        )

        result: GuardrailResult | None = None
        for guardrail in guardrails:
            r = guardrail.evaluate(ctx)
            if r.verdict != GuardrailVerdict.ACCEPT:
                result = r
                break  # First non-ACCEPT wins

        if result is None:
            # All guardrails accepted — done
            break

        meta.guardrail_triggered = True
        meta.guardrail_name = result.guardrail_name

        if result.verdict == GuardrailVerdict.OVERRIDE:
            if result.override_outcome is not None:
                current_outcome = result.override_outcome
                current_fallback = False  # Override is a guardrail decision, not fallback
                meta.safe_default_applied = True
                meta.final_outcome_kind = current_outcome.get("kind", "")
                logger.info(
                    "[Guardrail] Override applied: %s -> %s (reason: %s)",
                    meta.original_outcome_kind,
                    meta.final_outcome_kind,
                    result.reason,
                )
            break

        if result.verdict == GuardrailVerdict.NUDGE_RETRY:
            if attempt >= max_retries:
                # Should not happen (guardrail should return OVERRIDE), but be safe
                break

            meta.nudge_attempted = True
            nudge_msg = result.nudge_message
            if nudge_msg is None:
                logger.warning("[Guardrail] NUDGE_RETRY without nudge_message; skipping.")
                break

            # Re-invoke the agent with nudge appended
            try:
                retry_messages = list(current_messages) + [nudge_msg]
                retry_nms = len(retry_messages)

                logger.info(
                    "[Guardrail] Nudge re-invocation for task '%s' agent='%s' "
                    "(attempt=%d, prior_msgs=%d)",
                    task.get("task_id", "?"),
                    agent_name,
                    attempt,
                    len(current_messages),
                )

                agent = make_agent_fn(agent_config)
                retry_result = await agent.ainvoke(
                    {"messages": retry_messages},
                    config=agent_config,
                )
                retry_all_messages = retry_result.get("messages") or []

                # Clamp new_messages_start
                if retry_nms >= len(retry_all_messages):
                    retry_nms = 0

                # Re-classify
                retry_outcome, retry_fallback = normalize_agent_outcome(
                    task=task,
                    messages=retry_all_messages,
                    new_messages_start=retry_nms,
                )

                current_outcome = retry_outcome
                current_fallback = retry_fallback
                current_messages = retry_all_messages
                current_nms = retry_nms

                if not retry_fallback:
                    meta.nudge_succeeded = True
                    meta.final_outcome_kind = current_outcome.get("kind", "")
                    meta.nudge_messages = current_messages
                    meta.nudge_new_messages_start = current_nms
                    logger.info(
                        "[Guardrail] Nudge succeeded: agent called terminal tool. "
                        "kind=%s",
                        meta.final_outcome_kind,
                    )
                    break
                else:
                    logger.info(
                        "[Guardrail] Nudge did not produce structured output; "
                        "will evaluate next attempt. kind=%s",
                        current_outcome.get("kind", "?"),
                    )
                    # Continue to next attempt (which will trigger OVERRIDE)

            except Exception:
                logger.exception(
                    "[Guardrail] Nudge re-invocation failed for task '%s'; "
                    "will fall through to safe default.",
                    task.get("task_id", "?"),
                )
                # Continue to next attempt — guardrail will return OVERRIDE
                continue

    meta.final_outcome_kind = current_outcome.get("kind", "")
    return current_outcome, current_fallback, meta


__all__ = [
    "GuardrailContext",
    "GuardrailMetadata",
    "GuardrailResult",
    "GuardrailVerdict",
    "OutputGuardrail",
    "run_output_guardrails",
]
