"""Verification hook adapters: migrate Phase 4 verifiers into runtime hook handlers.

These adapters call the *existing* verification runtime API and translate the
verdict into a ``RuntimeHookResult`` that the hook runner understands.  The
verifier contract, registry, and domain verifier families are NOT touched.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .base import (
    HookDecision,
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookResult,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Task-level verification hook  (after_task_complete)
# ---------------------------------------------------------------------------

class TaskVerificationHook(RuntimeHookHandler):
    """Run task-level verification when a task completes successfully.

    Expected ``metadata`` keys (set by executor before calling the hook):
    - task: TaskStatus dict of the completing task
    - assigned_agent: str
    - task_result: str — the agent's output text
    - resolved_inputs: dict | None
    - artifacts: list[str]
    - verified_facts: dict

    The handler reads these from ``ctx.metadata`` and invokes the existing
    ``run_task_verification`` API.  Based on the verdict it returns:
    - passed  → continue (no patch, verification fields added)
    - needs_replan → short_circuit with FAILED + feedback
    - hard_fail → short_circuit with ERROR
    """

    name = "task_verification"
    priority = 50  # run after any future observability hooks (lower priority)

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        from src.verification.base import VerificationScope, VerificationVerdict
        from src.verification.runtime import (
            build_verification_feedback,
            run_task_verification,
        )

        meta = ctx.metadata
        task: dict[str, Any] = meta.get("task") or {}
        task_id = task.get("task_id", "")
        task_description = task.get("description", "")
        task_result = meta.get("task_result", "")
        assigned_agent = meta.get("assigned_agent")
        resolved_inputs = meta.get("resolved_inputs")
        verified_facts = meta.get("verified_facts") or {}
        artifacts = meta.get("artifacts") or []

        v_result = run_task_verification(
            task_id=task_id,
            task_description=task_description,
            task_result=task_result,
            assigned_agent=assigned_agent,
            resolved_inputs=resolved_inputs,
            verified_facts=verified_facts,
            artifacts=artifacts,
        )

        # --- HARD_FAIL -------------------------------------------------------
        if v_result.verdict == VerificationVerdict.HARD_FAIL:
            logger.error(
                "[TaskVerificationHook] Task '%s' HARD_FAIL: %s",
                task_id, v_result.report.summary,
            )
            hard_fail_task = {
                **task,
                "status": "FAILED",
                "result": task_result,
                "error": f"Verification hard_fail: {v_result.report.summary}",
                "status_detail": "@verification_hard_fail",
                "verification_status": "hard_fail",
                "verification_report": v_result.report.model_dump(),
                "clarification_prompt": None,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": None,
                "intercepted_tool_call": None,
                "continuation_mode": None,
                "pending_interrupt": None,
                "pending_tool_call": None,
                "updated_at": _utc_now_iso(),
            }
            return RuntimeHookResult.short_circuit(
                patch={
                    "task_pool": [hard_fail_task],
                    "execution_state": "ERROR",
                    "final_result": f"Verification hard failure on task '{task_id}': {v_result.report.summary}",
                    "workflow_verification_status": "hard_fail",
                    "workflow_verification_report": v_result.report.model_dump(),
                },
                reason=f"task_verification_hard_fail:{task_id}",
            )

        # --- NEEDS_REPLAN -----------------------------------------------------
        if v_result.verdict == VerificationVerdict.NEEDS_REPLAN:
            logger.warning(
                "[TaskVerificationHook] Task '%s' NEEDS_REPLAN: %s",
                task_id, v_result.report.summary,
            )
            replan_task = {
                **task,
                "status": "FAILED",
                "result": task_result,
                "error": f"Verification needs_replan: {v_result.report.summary}",
                "status_detail": "@verification_needs_replan",
                "verification_status": "needs_replan",
                "verification_report": v_result.report.model_dump(),
                "clarification_prompt": None,
                "clarification_request": None,
                "request_help": None,
                "blocked_reason": None,
                "agent_messages": None,
                "intercepted_tool_call": None,
                "continuation_mode": None,
                "pending_interrupt": None,
                "pending_tool_call": None,
                "updated_at": _utc_now_iso(),
            }
            feedback = build_verification_feedback(
                v_result, VerificationScope.TASK_RESULT, task_id,
            )
            return RuntimeHookResult.short_circuit(
                patch={
                    "task_pool": [replan_task],
                    "verification_feedback": feedback,
                    "execution_state": "EXECUTING_DONE",
                },
                reason=f"task_verification_needs_replan:{task_id}",
            )

        # --- PASSED -----------------------------------------------------------
        logger.info(
            "[TaskVerificationHook] Task '%s' PASSED.",
            task_id,
        )
        return RuntimeHookResult.ok(
            patch={
                "_verification_result": v_result.report.model_dump(),
            },
            reason=f"task_verification_passed:{task_id}",
        )


# ---------------------------------------------------------------------------
# Workflow-final verification hook  (before_final_result_commit)
# ---------------------------------------------------------------------------

class WorkflowVerificationHook(RuntimeHookHandler):
    """Run workflow-final verification before committing DONE.

    Expected ``metadata`` keys (set by planner before calling the hook):
    - final_result: str
    - task_pool: list[dict]
    - verified_facts: dict
    - workflow_kind: str | None
    - verification_retry_count: int
    - original_input: str
    - run_id: str
    - planner_goal: str

    Verdict translation mirrors the current planner inline logic exactly.
    """

    name = "workflow_verification"
    priority = 50

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        from src.verification.base import VerificationScope, VerificationVerdict
        from src.verification.runtime import (
            build_verification_feedback,
            check_retry_budget,
            run_workflow_verification,
        )

        meta = ctx.metadata
        final_result = meta.get("final_result", "")
        task_pool = meta.get("task_pool") or []
        verified_facts = meta.get("verified_facts") or {}
        workflow_kind = meta.get("workflow_kind")
        verification_retry_count = meta.get("verification_retry_count") or 0
        original_input = meta.get("original_input", "")
        run_id = meta.get("run_id")
        planner_goal = meta.get("planner_goal", "")

        task_pool_dicts = [dict(t) for t in task_pool]
        v_result = run_workflow_verification(
            final_result=final_result,
            task_pool=task_pool_dicts,
            verified_facts=verified_facts,
            workflow_kind=workflow_kind,
        )

        # --- HARD_FAIL -------------------------------------------------------
        if v_result.verdict == VerificationVerdict.HARD_FAIL:
            logger.error(
                "[WorkflowVerificationHook] HARD_FAIL: %s",
                v_result.report.summary,
            )
            return RuntimeHookResult.short_circuit(
                patch={
                    "execution_state": "ERROR",
                    "final_result": f"Verification hard failure: {v_result.report.summary}",
                    "messages": [],  # clear leaked messages from candidate
                    "verification_feedback": None,
                    "workflow_verification_status": "hard_fail",
                    "workflow_verification_report": v_result.report.model_dump(),
                },
                reason="workflow_verification_hard_fail",
            )

        # --- NEEDS_REPLAN -----------------------------------------------------
        if v_result.verdict == VerificationVerdict.NEEDS_REPLAN:
            verification_retry_count += 1
            if not check_retry_budget(verification_retry_count):
                logger.error(
                    "[WorkflowVerificationHook] Retry budget exhausted (%d retries).",
                    verification_retry_count,
                )
                return RuntimeHookResult.short_circuit(
                    patch={
                        "execution_state": "ERROR",
                        "final_result": f"Verification retry budget exhausted after {verification_retry_count} retries: {v_result.report.summary}",
                        "messages": [],  # clear leaked messages from candidate
                        "verification_feedback": None,
                        "verification_retry_count": verification_retry_count,
                        "workflow_verification_status": "hard_fail",
                        "workflow_verification_report": v_result.report.model_dump(),
                    },
                    reason="workflow_verification_budget_exhausted",
                )

            logger.warning(
                "[WorkflowVerificationHook] NEEDS_REPLAN (retry %d): %s",
                verification_retry_count, v_result.report.summary,
            )
            feedback = build_verification_feedback(
                v_result, VerificationScope.WORKFLOW_RESULT, "workflow",
            )
            return RuntimeHookResult.short_circuit(
                patch={
                    "task_pool": [],
                    "execution_state": "QUEUED",
                    "final_result": None,  # clear leaked final_result from candidate
                    "original_input": original_input,
                    "run_id": run_id,
                    "planner_goal": planner_goal,
                    "verification_feedback": feedback,
                    "verification_retry_count": verification_retry_count,
                    "workflow_verification_status": "needs_replan",
                    "workflow_verification_report": v_result.report.model_dump(),
                },
                reason="workflow_verification_needs_replan",
            )

        # --- PASSED -----------------------------------------------------------
        logger.info("[WorkflowVerificationHook] PASSED.")
        return RuntimeHookResult.ok(
            patch={
                "workflow_verification_status": "passed",
                "workflow_verification_report": v_result.report.model_dump(),
                "verification_feedback": None,
                "verification_retry_count": 0,
            },
            reason="workflow_verification_passed",
        )


# ---------------------------------------------------------------------------
# Default hook installation
# ---------------------------------------------------------------------------

def install_default_runtime_hooks(registry: Any | None = None) -> None:
    """Register the default verification hooks on the given (or global) registry.

    Idempotent: skips installation only if the *specific* default handler is
    already present.  Custom handlers on the same hook point do NOT prevent
    the default verifier from being installed.
    """
    from .base import RuntimeHookName
    from .registry import runtime_hook_registry

    reg = registry or runtime_hook_registry

    if not reg.has_handler_named(RuntimeHookName.AFTER_TASK_COMPLETE, TaskVerificationHook.name):
        reg.register(RuntimeHookName.AFTER_TASK_COMPLETE, TaskVerificationHook())

    if not reg.has_handler_named(RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT, WorkflowVerificationHook.name):
        reg.register(RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT, WorkflowVerificationHook())

    logger.info("[RuntimeHooks] Default verification hooks installed.")
