"""Runtime hook contract: core types shared by registry, runner, and all hook handlers."""

from __future__ import annotations

import abc
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Hook point names
# ---------------------------------------------------------------------------

class RuntimeHookName(str, Enum):
    """Enumeration of all recognised runtime hook points.

    Only the five Slice-A hooks are active in this MVP.  Slice-B names are
    listed as documentation placeholders but NOT registered automatically.
    """

    # Slice A — active
    AFTER_PLANNER = "after_planner"
    AFTER_ROUTER = "after_router"
    AFTER_EXECUTOR = "after_executor"
    AFTER_TASK_COMPLETE = "after_task_complete"
    BEFORE_FINAL_RESULT_COMMIT = "before_final_result_commit"

    # Slice B — reserved, not implemented in this MVP
    # BEFORE_INTERRUPT_EMIT = "before_interrupt_emit"
    # AFTER_INTERRUPT_RESOLVE = "after_interrupt_resolve"
    # BEFORE_TASK_POOL_COMMIT = "before_task_pool_commit"
    # BEFORE_VERIFIED_FACTS_COMMIT = "before_verified_facts_commit"


# ---------------------------------------------------------------------------
# Hook decision enum
# ---------------------------------------------------------------------------

class HookDecision(str, Enum):
    """What the runner should do after a handler returns."""

    CONTINUE = "continue"
    SHORT_CIRCUIT = "short_circuit"


# ---------------------------------------------------------------------------
# Runtime hook context — input to every handler
# ---------------------------------------------------------------------------

class RuntimeHookContext:
    """Immutable context object passed to every runtime hook handler.

    *state* is a read-only snapshot; handlers MUST NOT mutate it.
    *proposed_update* is the dict the node intends to return to the graph.
    *metadata* carries hook-point-specific structured data.
    """

    __slots__ = (
        "hook_name",
        "node_name",
        "run_id",
        "thread_id",
        "state",
        "proposed_update",
        "metadata",
    )

    def __init__(
        self,
        *,
        hook_name: RuntimeHookName,
        node_name: str,
        run_id: str | None = None,
        thread_id: str | None = None,
        state: dict[str, Any] | None = None,
        proposed_update: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.hook_name = hook_name
        self.node_name = node_name
        self.run_id = run_id
        self.thread_id = thread_id
        self.state = state or {}
        self.proposed_update = dict(proposed_update) if proposed_update else {}
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# Runtime hook result — output from every handler
# ---------------------------------------------------------------------------

class RuntimeHookResult:
    """Value returned by a hook handler to the runner."""

    __slots__ = ("decision", "update_patch", "reason")

    def __init__(
        self,
        *,
        decision: HookDecision = HookDecision.CONTINUE,
        update_patch: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        self.decision = decision
        self.update_patch = update_patch or {}
        self.reason = reason

    # Convenience factories ---------------------------------------------------

    @classmethod
    def ok(cls, patch: dict[str, Any] | None = None, reason: str | None = None) -> RuntimeHookResult:
        """Continue the hook chain, optionally patching proposed_update."""
        return cls(decision=HookDecision.CONTINUE, update_patch=patch, reason=reason)

    @classmethod
    def short_circuit(cls, patch: dict[str, Any] | None = None, reason: str | None = None) -> RuntimeHookResult:
        """Stop the hook chain early, optionally patching proposed_update."""
        return cls(decision=HookDecision.SHORT_CIRCUIT, update_patch=patch, reason=reason)


# ---------------------------------------------------------------------------
# Abstract handler base class
# ---------------------------------------------------------------------------

class RuntimeHookHandler(abc.ABC):
    """Base class for all runtime hook handlers.

    Subclasses MUST implement :meth:`handle` and MAY override ``name`` /
    ``priority``.
    """

    name: str = "base_hook_handler"
    priority: int = 100  # lower runs first

    @abc.abstractmethod
    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        """Execute the hook logic and return a result.

        Implementations MUST NOT mutate ``ctx.state``.  They MAY read
        ``ctx.proposed_update`` (which already includes patches from earlier
        handlers in the chain) and return an ``update_patch`` to be shallow-
        merged on top.
        """
        ...
