"""Runtime hook runner: execute registered handlers for a given hook point."""

from __future__ import annotations

import copy
import logging
from typing import Any

from src.observability.tracer import span

from .base import (
    HookDecision,
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookName,
    RuntimeHookResult,
)
from .registry import RuntimeHookRegistry, runtime_hook_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy default-hook installation (fixes P1: direct node calls)
# ---------------------------------------------------------------------------


def ensure_default_hooks() -> None:
    """Ensure default verification hooks are present on the global registry.

    Delegates to :func:`install_default_runtime_hooks` which is idempotent
    (checks by handler name, not by a boolean flag).  This means the function
    is safe to call after ``registry.clear()`` — the defaults will simply be
    re-installed on the next hook invocation.

    Called automatically by :func:`run_runtime_hooks` before every execution
    when no explicit *registry* is provided, so nodes invoked directly
    (outside the compiled graph) still get correct verifier behaviour.
    """
    from .verification_hooks import install_default_runtime_hooks
    install_default_runtime_hooks()

    # Phase 5A: install governance audit hooks alongside verification hooks
    from src.agents.governance.audit_hooks import install_governance_audit_hooks
    install_governance_audit_hooks()


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class HookExecutionError(Exception):
    """Raised when a runtime hook handler fails.

    Carries structured information so that the calling node can build an
    appropriate error-state update without string-parsing.
    """

    def __init__(
        self,
        hook_name: RuntimeHookName,
        handler_name: str,
        cause: Exception,
    ) -> None:
        self.hook_name = hook_name
        self.handler_name = handler_name
        self.cause = cause
        super().__init__(
            f"Hook '{hook_name.value}' handler '{handler_name}' raised: {cause}"
        )


# ---------------------------------------------------------------------------
# Shallow merge helper
# ---------------------------------------------------------------------------

def _shallow_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Top-level shallow merge: patch keys overwrite base keys."""
    merged = dict(base)
    merged.update(patch)
    return merged


# ---------------------------------------------------------------------------
# Deep-copy state snapshot
# ---------------------------------------------------------------------------

def _snapshot_state(state: dict[str, Any]) -> dict[str, Any]:
    """Create a deep copy of *state* so that handlers cannot mutate the
    original graph state.  Falls back to a shallow copy on non-picklable
    objects (e.g. LangChain message instances)."""
    try:
        return copy.deepcopy(state)
    except Exception:
        # Shallow copy as last resort — still isolates top-level keys
        return dict(state)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_runtime_hooks(
    hook_name: RuntimeHookName,
    *,
    node_name: str,
    state: dict[str, Any],
    proposed_update: dict[str, Any],
    run_id: str | None = None,
    thread_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    registry: RuntimeHookRegistry | None = None,
) -> dict[str, Any]:
    """Execute all handlers registered for *hook_name* and return the final proposed_update.

    Execution rules (frozen for this MVP):
    1. Handlers run synchronously in priority order (low -> high).
    2. Each handler sees the *proposed_update* accumulated so far.
    3. ``continue`` merges *update_patch* and proceeds to the next handler.
    4. ``short_circuit`` merges *update_patch* and stops immediately.
    5. If a handler raises an exception the chain stops and a
       :class:`HookExecutionError` is propagated — the caller MUST convert
       this into an appropriate error state (fail-closed behaviour).

    Returns the (potentially modified) *proposed_update* dict.  When no
    handlers are registered the input *proposed_update* is returned as-is,
    guaranteeing zero-behaviour-change for an empty registry.
    """
    # Ensure default hooks are installed even when called outside graph compile
    if registry is None:
        ensure_default_hooks()

    reg = registry or runtime_hook_registry
    handlers: list[RuntimeHookHandler] = reg.get_handlers(hook_name)

    if not handlers:
        return proposed_update

    current_update = dict(proposed_update)
    state_snapshot = _snapshot_state(state)

    with span(
        f"hook.{hook_name.value}",
        attributes={
            "hook_name": hook_name.value,
            "node_name": node_name,
            "handler_count": len(handlers),
            "run_id": run_id or "",
        },
    ) as hook_span:
        for idx, handler in enumerate(handlers):
            ctx = RuntimeHookContext(
                hook_name=hook_name,
                node_name=node_name,
                run_id=run_id,
                thread_id=thread_id,
                state=state_snapshot,
                proposed_update=current_update,
                metadata=metadata or {},
            )
            try:
                result: RuntimeHookResult = handler.handle(ctx)
            except Exception as exc:
                logger.error(
                    "[HookRunner] Handler '%s' for hook '%s' raised: %s",
                    handler.name, hook_name.value, exc,
                    exc_info=True,
                )
                hook_span.set_attribute("hook_error", True)
                hook_span.set_attribute("hook_error_handler", handler.name)
                raise HookExecutionError(hook_name, handler.name, exc) from exc

            if result.update_patch:
                current_update = _shallow_merge(current_update, result.update_patch)

            logger.info(
                "[HookRunner] hook=%s handler=%s [%d/%d] decision=%s reason=%s patch_keys=%s",
                hook_name.value,
                handler.name,
                idx + 1,
                len(handlers),
                result.decision.value,
                result.reason,
                list(result.update_patch.keys()) if result.update_patch else [],
            )

            if result.decision == HookDecision.SHORT_CIRCUIT:
                hook_span.set_attribute("short_circuited", True)
                hook_span.set_attribute("short_circuit_handler", handler.name)
                hook_span.set_attribute("short_circuit_reason", result.reason or "")
                break

        hook_span.set_attribute("handlers_executed", idx + 1 if handlers else 0)

    return current_update
