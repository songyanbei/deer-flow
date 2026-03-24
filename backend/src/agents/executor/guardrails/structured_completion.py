"""StructuredCompletionGuardrail — enforce terminal tool calling discipline.

This is the primary output guardrail.  It detects when a domain agent
violated the structured output contract (no ``task_complete``,
``task_fail``, or ``request_help`` tool call) and either nudges the agent
for a retry or applies a safe default.
"""

from __future__ import annotations

import logging

from .base import (
    GuardrailContext,
    GuardrailResult,
    GuardrailVerdict,
    OutputGuardrail,
)
from .nudge import build_nudge_message
from .safe_default import apply_safe_default

logger = logging.getLogger(__name__)


class StructuredCompletionGuardrail(OutputGuardrail):
    """Ensure domain agent output contains a terminal tool signal."""

    name = "structured_completion"
    priority = 0  # Highest priority — evaluated first

    # Fallback outcome kinds that are already safe terminal states.
    # These don't need guardrail intervention even when used_fallback=True.
    _SAFE_FALLBACK_KINDS = frozenset({"fail"})

    def evaluate(self, ctx: GuardrailContext) -> GuardrailResult:
        # Only trigger when classification relied on heuristic fallback
        if not ctx.used_fallback:
            return GuardrailResult(
                verdict=GuardrailVerdict.ACCEPT,
                guardrail_name=self.name,
                reason="Agent used explicit tool signal; no guardrail needed.",
            )

        # A fallback "fail" (e.g. agent returned no output) is already a
        # safe terminal state — no need to nudge or override.
        outcome_kind = ctx.outcome.get("kind", "")
        if outcome_kind in self._SAFE_FALLBACK_KINDS:
            return GuardrailResult(
                verdict=GuardrailVerdict.ACCEPT,
                guardrail_name=self.name,
                reason=f"Fallback kind '{outcome_kind}' is a safe terminal state.",
            )

        # Attempt 0: request a nudge retry
        if ctx.attempt < ctx.max_retries:
            agent_output = (
                ctx.outcome.get("result_text", "")
                or ctx.outcome.get("prompt", "")
                or ""
            )
            nudge = build_nudge_message(agent_output)
            logger.info(
                "[Guardrail:%s] Nudge requested for task '%s' (attempt=%d, "
                "original_kind=%s, output_len=%d)",
                self.name,
                ctx.task.get("task_id", "?"),
                ctx.attempt,
                ctx.outcome.get("kind", "?"),
                len(agent_output),
            )
            return GuardrailResult(
                verdict=GuardrailVerdict.NUDGE_RETRY,
                guardrail_name=self.name,
                reason="Agent did not call terminal tool; nudging for structured output.",
                nudge_message=nudge,
            )

        # Attempt >= max_retries: override with safe default
        override = apply_safe_default(
            outcome=ctx.outcome,
            messages=ctx.messages,
            new_messages_start=ctx.new_messages_start,
        )
        logger.info(
            "[Guardrail:%s] Safe default applied for task '%s' "
            "(attempts_exhausted=%d, original_kind=%s -> complete)",
            self.name,
            ctx.task.get("task_id", "?"),
            ctx.attempt,
            ctx.outcome.get("kind", "?"),
        )
        return GuardrailResult(
            verdict=GuardrailVerdict.OVERRIDE,
            guardrail_name=self.name,
            reason="Nudge retry exhausted; applying safe default (complete).",
            override_outcome=override,
        )
