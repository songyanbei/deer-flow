"""Output guardrail contracts: base class, context, verdict, and result types.

All output guardrails share these types.  A guardrail inspects the outcome
produced by ``normalize_agent_outcome`` and decides whether to accept it,
nudge the agent for a structured retry, or override the outcome entirely.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from src.agents.executor.outcome import AgentOutcome
from src.agents.thread_state import TaskStatus


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------

class GuardrailVerdict(str, Enum):
    """What the guardrail recommends."""

    ACCEPT = "accept"
    """Outcome is acceptable — proceed to branching."""

    NUDGE_RETRY = "nudge_retry"
    """Inject a nudge message, re-invoke the agent, and re-classify."""

    OVERRIDE = "override"
    """Replace the outcome with a guardrail-provided alternative."""


# ---------------------------------------------------------------------------
# Context passed to guardrails
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuardrailContext:
    """Immutable snapshot of the execution state at guardrail evaluation time."""

    task: TaskStatus
    agent_name: str
    messages: list[Any]
    new_messages_start: int
    outcome: AgentOutcome
    used_fallback: bool
    attempt: int  # 0 = first evaluation, 1 = after nudge retry
    agent_config: RunnableConfig
    max_retries: int = 1


# ---------------------------------------------------------------------------
# Result returned by a guardrail
# ---------------------------------------------------------------------------

@dataclass
class GuardrailResult:
    """Single guardrail evaluation result."""

    verdict: GuardrailVerdict
    guardrail_name: str
    reason: str
    nudge_message: HumanMessage | None = None
    override_outcome: AgentOutcome | None = None


# ---------------------------------------------------------------------------
# Metadata emitted after the guardrail gate completes
# ---------------------------------------------------------------------------

@dataclass
class GuardrailMetadata:
    """Structured metadata for observability and decision tracking."""

    guardrail_triggered: bool = False
    guardrail_name: str | None = None
    nudge_attempted: bool = False
    nudge_succeeded: bool = False
    safe_default_applied: bool = False
    original_outcome_kind: str = ""
    final_outcome_kind: str = ""
    nudge_messages: list[Any] | None = field(default=None, repr=False)
    nudge_new_messages_start: int = 0


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class OutputGuardrail(abc.ABC):
    """Base class for all output guardrails.

    Subclasses implement ``evaluate`` which inspects the guardrail context
    and returns a verdict.  The guardrail runner calls ``evaluate`` once per
    attempt (attempt=0 for initial check, attempt=1 after a nudge retry).
    Implementations **must** be idempotent and side-effect-free.
    """

    name: str = "base_guardrail"
    priority: int = 0  # Lower = evaluated first

    @abc.abstractmethod
    def evaluate(self, ctx: GuardrailContext) -> GuardrailResult:
        """Evaluate whether this guardrail should intervene."""
        ...
