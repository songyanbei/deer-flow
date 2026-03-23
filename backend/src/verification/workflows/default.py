"""Default workflow-final verifier for cross-domain workflow summary."""

from __future__ import annotations

from src.verification.base import (
    BaseVerifier,
    VerificationContext,
    VerificationFinding,
    VerificationResult,
    VerificationScope,
    VerificationVerdict,
)


class DefaultWorkflowVerifier(BaseVerifier):
    """Verifies the workflow-final summary before allowing DONE status."""

    name = "default_workflow_verifier"
    scope = VerificationScope.WORKFLOW_RESULT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        findings: list[VerificationFinding] = []

        summary = (ctx.final_result or "").strip()

        # 1. Must have non-empty summary
        if not summary:
            findings.append(VerificationFinding(
                field="final_result",
                severity="error",
                message="Workflow final summary is empty.",
            ))

        # 2. Summary should have meaningful length
        if summary and len(summary) < 20:
            findings.append(VerificationFinding(
                field="final_result",
                severity="warning",
                message=f"Workflow summary is suspiciously short ({len(summary)} chars).",
                actual=summary,
            ))

        # 3. Check that tasks reached terminal states
        task_pool = ctx.task_pool or []
        if task_pool:
            done_count = sum(1 for t in task_pool if t.get("status") == "DONE")
            failed_count = sum(1 for t in task_pool if t.get("status") == "FAILED")
            terminal_count = done_count + failed_count
            total = len(task_pool)
            non_terminal = total - terminal_count
            if non_terminal > 0:
                findings.append(VerificationFinding(
                    field="task_pool",
                    severity="error",
                    message=f"{non_terminal} task(s) still in non-terminal state out of {total} total.",
                    expected="all tasks in terminal state (DONE or FAILED)",
                    actual=f"done={done_count}, failed={failed_count}, non_terminal={non_terminal}, total={total}",
                ))
            elif done_count == 0 and failed_count > 0:
                # All tasks failed - this is a valid terminal state (e.g. intervention rejected)
                # but emit a warning since the summary may only reflect failures
                findings.append(VerificationFinding(
                    field="task_pool",
                    severity="warning",
                    message=f"All {failed_count} task(s) failed. Summary reflects failure outcomes.",
                    actual=f"done={done_count}, failed={failed_count}, total={total}",
                ))
            elif failed_count > 0:
                findings.append(VerificationFinding(
                    field="task_pool",
                    severity="warning",
                    message=f"{failed_count} task(s) failed out of {total}. Summary may be incomplete.",
                    actual=f"done={done_count}, failed={failed_count}, total={total}",
                ))

        # 4. Check verified_facts consistency
        if task_pool and not ctx.verified_facts:
            findings.append(VerificationFinding(
                field="verified_facts",
                severity="warning",
                message="Workflow has tasks but no verified facts collected.",
            ))

        errors = [f for f in findings if f.severity == "error"]
        if errors:
            return self._fail(
                VerificationVerdict.NEEDS_REPLAN,
                f"Workflow-final verification failed with {len(errors)} error(s).",
                findings,
            )
        return self._pass("Workflow summary verified.", findings=findings)
