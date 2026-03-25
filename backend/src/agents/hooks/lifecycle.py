"""Slice B lifecycle helpers: interrupt emit, interrupt resolve, and state commit hooks.

These helpers encapsulate the hook invocation logic so that executor, router,
and gateway can share the same calling convention without duplicating metadata
assembly or error handling.

All helpers follow the same contract as :func:`run_runtime_hooks`:

- Empty registry → zero-behaviour-change (original data returned as-is).
- Handler exception → ``HookExecutionError`` propagated to caller.
- ``short_circuit`` → remaining handlers skipped, patch applied.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import RuntimeHookName
from .runner import run_runtime_hooks

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# before_interrupt_emit
# ---------------------------------------------------------------------------

def apply_before_interrupt_emit(
    *,
    interrupt_type: str,
    task: dict[str, Any],
    agent_name: str,
    source_path: str,
    proposed_update: dict[str, Any],
    state: dict[str, Any] | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``BEFORE_INTERRUPT_EMIT`` hooks before an interrupt event is emitted.

    Parameters
    ----------
    interrupt_type : str
        One of ``"intervention"``, ``"clarification"``, ``"dependency"``.
    task : dict
        The task dict that will be part of the interrupt.
    agent_name : str
        The agent triggering the interrupt.
    source_path : str
        Stable identifier for the call site, e.g. ``"executor.request_intervention"``
        or ``"router._interrupt_for_clarification"``.
    proposed_update : dict
        The graph update dict that the caller intends to return.
    state : dict, optional
        Current graph state snapshot (read-only).
    run_id, thread_id : str, optional
        Correlation identifiers.
    extra_metadata : dict, optional
        Additional metadata merged into the hook context.

    Returns
    -------
    dict
        The (potentially modified) *proposed_update*.
    """
    metadata: dict[str, Any] = {
        "interrupt_type": interrupt_type,
        "task_id": task.get("task_id", ""),
        "agent_name": agent_name,
        "source_path": source_path,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return run_runtime_hooks(
        RuntimeHookName.BEFORE_INTERRUPT_EMIT,
        node_name=source_path.split(".")[0] if "." in source_path else "unknown",
        state=state or {},
        proposed_update=proposed_update,
        run_id=run_id,
        thread_id=thread_id,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# after_interrupt_resolve
# ---------------------------------------------------------------------------

def apply_after_interrupt_resolve(
    *,
    task: dict[str, Any],
    resolution: dict[str, Any] | None = None,
    source_path: str,
    proposed_update: dict[str, Any],
    state: dict[str, Any] | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``AFTER_INTERRUPT_RESOLVE`` hooks after an intervention is resolved.

    Covers both:
    - In-graph resume path (router detects resolution message)
    - Gateway direct-write path (``update_state()`` after resolve endpoint)

    Parameters
    ----------
    task : dict
        The resolved task (already transitioned to RUNNING / FAILED).
    resolution : dict, optional
        The intervention resolution record.
    source_path : str
        e.g. ``"router.in_graph_resolve"`` or ``"gateway.resolve_intervention"``.
    proposed_update : dict
        The update dict about to be committed.
    """
    metadata: dict[str, Any] = {
        "task_id": task.get("task_id", ""),
        "new_status": task.get("status", ""),
        "source_path": source_path,
    }
    if resolution:
        metadata["action_key"] = resolution.get("action_key", "")
        metadata["resolution_behavior"] = resolution.get("resolution_behavior", "")
        metadata["request_id"] = resolution.get("request_id", "")
    if extra_metadata:
        metadata.update(extra_metadata)

    return run_runtime_hooks(
        RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
        node_name=source_path.split(".")[0] if "." in source_path else "unknown",
        state=state or {},
        proposed_update=proposed_update,
        run_id=run_id,
        thread_id=thread_id,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# State commit hooks (task_pool + verified_facts)
# ---------------------------------------------------------------------------

class VerifiedFactsClearAllGuardError(Exception):
    """Raised when a proposed update contains ``verified_facts={}`` (clear-all)
    without an explicit ``allow_verified_facts_clear_all=True`` flag."""


def apply_state_commit_hooks(
    *,
    proposed_update: dict[str, Any],
    state: dict[str, Any] | None = None,
    source_path: str,
    run_id: str | None = None,
    thread_id: str | None = None,
    allow_verified_facts_clear_all: bool = False,
) -> dict[str, Any]:
    """Run ``BEFORE_TASK_POOL_COMMIT`` and ``BEFORE_VERIFIED_FACTS_COMMIT``
    hooks on the proposed update, in that fixed order.

    The hooks fire only when the corresponding key is present in
    *proposed_update*:

    - ``task_pool`` present → ``BEFORE_TASK_POOL_COMMIT``
    - ``verified_facts`` present → ``BEFORE_VERIFIED_FACTS_COMMIT``

    The reducer remains the sole merge authority — hooks may only patch
    ``proposed_update``, never bypass the reducer.

    Parameters
    ----------
    proposed_update : dict
        The candidate state update.  Modified in place via shallow merge.
    state : dict, optional
        Current graph state snapshot (read-only).
    source_path : str
        e.g. ``"node_wrapper.after_hooks"`` or ``"gateway.resolve_intervention"``.
    allow_verified_facts_clear_all : bool
        Must be ``True`` to allow ``verified_facts={}`` to pass through.
        Defaults to ``False`` for safety.

    Returns
    -------
    dict
        The (potentially modified) *proposed_update*.

    Raises
    ------
    VerifiedFactsClearAllGuardError
        If ``verified_facts`` is an empty dict and
        *allow_verified_facts_clear_all* is False.
    HookExecutionError
        If any registered handler raises.
    """
    effective_state = state or {}
    node_name = source_path.split(".")[0] if "." in source_path else "unknown"
    current = dict(proposed_update)

    # --- before_task_pool_commit ---
    if "task_pool" in current:
        current = run_runtime_hooks(
            RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name=node_name,
            state=effective_state,
            proposed_update=current,
            run_id=run_id,
            thread_id=thread_id,
            metadata={
                "source_path": source_path,
                "task_pool_size": len(current["task_pool"]) if isinstance(current.get("task_pool"), list) else 0,
                "commit_reason": "state_commit",
            },
        )

    # --- before_verified_facts_commit ---
    if "verified_facts" in current:
        vf = current["verified_facts"]
        # Safety guard: block accidental clear-all
        if isinstance(vf, dict) and len(vf) == 0 and not allow_verified_facts_clear_all:
            raise VerifiedFactsClearAllGuardError(
                "Proposed update contains verified_facts={} (clear-all). "
                "Set allow_verified_facts_clear_all=True to permit this."
            )

        current = run_runtime_hooks(
            RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT,
            node_name=node_name,
            state=effective_state,
            proposed_update=current,
            run_id=run_id,
            thread_id=thread_id,
            metadata={
                "source_path": source_path,
                "facts_count": len(vf) if isinstance(vf, dict) else 0,
                "is_clear_all": isinstance(vf, dict) and len(vf) == 0,
                "commit_reason": "state_commit",
            },
        )

    return current
