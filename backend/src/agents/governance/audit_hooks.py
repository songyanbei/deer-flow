"""Governance audit hook handlers — record governance events in the ledger.

These handlers register on existing runtime hook points to inject governance
audit context without modifying the core hook/lifecycle code.

Hook points covered:
- BEFORE_INTERRUPT_EMIT  → record interrupt emission
- AFTER_INTERRUPT_RESOLVE → record resolution and update ledger status
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.hooks.base import (
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookResult,
)

from .engine import GovernanceEngine, governance_engine

logger = logging.getLogger(__name__)


class GovernanceInterruptEmitAuditHook(RuntimeHookHandler):
    """Record governance audit entry when an interrupt is about to be emitted."""

    name = "governance_interrupt_emit_audit"
    priority = 200  # run after business hooks

    def __init__(self, engine: GovernanceEngine | None = None) -> None:
        self._engine = engine or governance_engine

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        metadata = ctx.metadata or {}
        thread_id = ctx.thread_id or ""
        run_id = ctx.run_id or ""
        task_id = metadata.get("task_id", "")
        agent_name = metadata.get("agent_name", "")
        interrupt_type = metadata.get("interrupt_type", "unknown")
        source_path = metadata.get("source_path", "unknown")

        # Extract request_id from the proposed task_pool update if available
        request_id = _extract_request_id_from_update(ctx.proposed_update)
        risk_level = _extract_risk_level_from_update(ctx.proposed_update)

        try:
            self._engine.record_interrupt_emit(
                thread_id=thread_id,
                run_id=run_id,
                task_id=task_id,
                source_agent=agent_name,
                interrupt_type=interrupt_type,
                source_path=source_path,
                risk_level=risk_level,
                request_id=request_id,
                action_summary=f"Interrupt emit: {interrupt_type} from {agent_name}",
                metadata={"hook_metadata": {k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))}},
            )
        except Exception:
            logger.exception("[GovernanceAuditHook] Failed to record interrupt emit audit")

        return RuntimeHookResult.ok()


class GovernanceInterruptResolveAuditHook(RuntimeHookHandler):
    """Record governance audit entry when an interrupt is resolved."""

    name = "governance_interrupt_resolve_audit"
    priority = 200  # run after business hooks

    def __init__(self, engine: GovernanceEngine | None = None) -> None:
        self._engine = engine or governance_engine

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        metadata = ctx.metadata or {}
        thread_id = ctx.thread_id or ""
        run_id = ctx.run_id or ""
        task_id = metadata.get("task_id", "")
        source_path = metadata.get("source_path", "unknown")
        action_key = metadata.get("action_key", "")
        resolution_behavior = metadata.get("resolution_behavior", "")
        request_id = metadata.get("request_id", "")

        # Determine resolved_by from source_path
        resolved_by = "operator" if "gateway" in source_path else "inline"

        try:
            self._engine.record_interrupt_resolve(
                thread_id=thread_id,
                run_id=run_id,
                task_id=task_id,
                source_agent="system",
                source_path=source_path,
                request_id=request_id,
                action_key=action_key,
                resolution_behavior=resolution_behavior,
                resolved_by=resolved_by,
                metadata={"hook_metadata": {k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))}},
            )
        except Exception:
            logger.exception("[GovernanceAuditHook] Failed to record interrupt resolve audit")

        return RuntimeHookResult.ok()


class GovernanceStateCommitAuditHook(RuntimeHookHandler):
    """Record governance audit entries for state-commit hook points."""

    name = "governance_state_commit_audit"
    priority = 200

    def __init__(self, engine: GovernanceEngine | None = None) -> None:
        self._engine = engine or governance_engine

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        metadata = ctx.metadata or {}
        thread_id = ctx.thread_id or ""
        run_id = ctx.run_id or ""
        source_path = metadata.get("source_path", "unknown")

        if ctx.hook_name.value == "before_task_pool_commit":
            commit_type = "task_pool"
        else:
            commit_type = "verified_facts"

        try:
            self._engine.record_state_commit_audit(
                thread_id=thread_id,
                run_id=run_id,
                source_path=source_path,
                commit_type=commit_type,
                metadata={
                    "hook_metadata": {
                        k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))
                    }
                },
            )
        except Exception:
            logger.exception("[GovernanceAuditHook] Failed to record state commit audit")

        return RuntimeHookResult.ok()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_request_id_from_update(proposed_update: dict[str, Any]) -> str | None:
    """Try to extract request_id from task_pool in the proposed update."""
    task_pool = proposed_update.get("task_pool")
    if not isinstance(task_pool, list):
        return None
    for task in task_pool:
        req = task.get("intervention_request")
        if isinstance(req, dict):
            return req.get("request_id")
    return None


def _extract_risk_level_from_update(proposed_update: dict[str, Any]) -> str:
    """Try to extract risk_level from intervention_request in proposed update."""
    task_pool = proposed_update.get("task_pool")
    if isinstance(task_pool, list):
        for task in task_pool:
            req = task.get("intervention_request")
            if isinstance(req, dict):
                return req.get("risk_level", "medium")
    return "medium"


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def install_governance_audit_hooks(registry: Any | None = None) -> None:
    """Register governance audit hooks on the given (or global) registry.

    Idempotent: each default governance handler is installed only if that
    specific handler name is not already present on its hook point.
    """
    from src.agents.hooks.base import RuntimeHookName
    from src.agents.hooks.registry import runtime_hook_registry

    reg = registry or runtime_hook_registry

    if not reg.has_handler_named(RuntimeHookName.BEFORE_INTERRUPT_EMIT, GovernanceInterruptEmitAuditHook.name):
        reg.register(RuntimeHookName.BEFORE_INTERRUPT_EMIT, GovernanceInterruptEmitAuditHook())

    if not reg.has_handler_named(RuntimeHookName.AFTER_INTERRUPT_RESOLVE, GovernanceInterruptResolveAuditHook.name):
        reg.register(RuntimeHookName.AFTER_INTERRUPT_RESOLVE, GovernanceInterruptResolveAuditHook())

    if not reg.has_handler_named(RuntimeHookName.BEFORE_TASK_POOL_COMMIT, GovernanceStateCommitAuditHook.name):
        reg.register(RuntimeHookName.BEFORE_TASK_POOL_COMMIT, GovernanceStateCommitAuditHook())

    if not reg.has_handler_named(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT, GovernanceStateCommitAuditHook.name):
        reg.register(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT, GovernanceStateCommitAuditHook())

    logger.info("[GovernanceAuditHooks] Governance audit hooks installed")
