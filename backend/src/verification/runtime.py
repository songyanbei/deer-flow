"""Runtime verification helpers: gate execution, feedback construction, retry budget."""

from __future__ import annotations

import logging
from typing import Any

from .base import (
    VerificationContext,
    VerificationFeedback,
    VerificationResult,
    VerificationScope,
    VerificationVerdict,
)
from .registry import verifier_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_VERIFICATION_RETRIES = 3


# ---------------------------------------------------------------------------
# Task-level verification gate
# ---------------------------------------------------------------------------

def run_task_verification(
    task_id: str,
    task_description: str,
    task_result: str,
    assigned_agent: str | None,
    resolved_inputs: dict[str, Any] | None,
    verified_facts: dict[str, Any],
    artifacts: list[str],
) -> VerificationResult:
    """Run task-level verification gate.

    Called by executor before writing to verified_facts.
    """
    verifier = verifier_registry.get_task_verifier(assigned_agent)

    ctx = VerificationContext(
        scope=VerificationScope.TASK_RESULT,
        domain=_extract_domain(assigned_agent),
        task_id=task_id,
        task_description=task_description,
        task_result=task_result,
        assigned_agent=assigned_agent,
        resolved_inputs=resolved_inputs,
        verified_facts=verified_facts,
        artifacts=artifacts,
    )

    logger.info(
        "[Verification] Running task verifier '%s' for task '%s' (agent=%s)",
        verifier.name, task_id, assigned_agent,
    )
    result = verifier.verify(ctx)
    logger.info(
        "[Verification] Task '%s' verdict=%s (verifier=%s)",
        task_id, result.verdict.value, verifier.name,
    )
    return result


# ---------------------------------------------------------------------------
# Workflow-final verification gate
# ---------------------------------------------------------------------------

def run_workflow_verification(
    final_result: str,
    task_pool: list[dict[str, Any]],
    verified_facts: dict[str, Any],
    workflow_kind: str | None = None,
) -> VerificationResult:
    """Run workflow-final verification gate.

    Called by planner before setting execution_state=DONE.
    """
    verifier = verifier_registry.get_workflow_verifier(workflow_kind)

    ctx = VerificationContext(
        scope=VerificationScope.WORKFLOW_RESULT,
        final_result=final_result,
        task_pool=task_pool,
        verified_facts=verified_facts,
        workflow_kind=workflow_kind or "default",
    )

    logger.info(
        "[Verification] Running workflow verifier '%s' (kind=%s)",
        verifier.name, workflow_kind or "default",
    )
    result = verifier.verify(ctx)
    logger.info(
        "[Verification] Workflow verdict=%s (verifier=%s)",
        result.verdict.value, verifier.name,
    )
    return result


# ---------------------------------------------------------------------------
# Feedback construction
# ---------------------------------------------------------------------------

def build_verification_feedback(
    result: VerificationResult,
    scope: VerificationScope,
    source_target: str,
) -> dict[str, Any]:
    """Build a structured verification_feedback dict from a VerificationResult."""
    feedback = VerificationFeedback(
        source_scope=scope,
        source_target=source_target,
        verifier_name=result.report.verifier_name,
        verdict=result.verdict,
        summary=result.report.summary,
        findings=result.report.findings,
        recommended_action=_verdict_to_action(result.verdict),
    )
    return feedback.model_dump()


def _verdict_to_action(verdict: VerificationVerdict) -> str:
    if verdict == VerificationVerdict.NEEDS_REPLAN:
        return "replan"
    if verdict == VerificationVerdict.HARD_FAIL:
        return "fail"
    return "none"


def _extract_domain(agent_name: str | None) -> str | None:
    if not agent_name:
        return None
    return agent_name.replace("-agent", "").replace("_agent", "")


# ---------------------------------------------------------------------------
# Retry budget check
# ---------------------------------------------------------------------------

def check_retry_budget(current_count: int) -> bool:
    """Return True if more retries are allowed.

    current_count is the count *after* incrementing for the current attempt.
    Allows exactly MAX_VERIFICATION_RETRIES replan attempts.
    """
    return current_count <= MAX_VERIFICATION_RETRIES
