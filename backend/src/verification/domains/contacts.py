"""Contacts domain task-level verifier."""

from __future__ import annotations

from src.verification.base import (
    BaseVerifier,
    VerificationContext,
    VerificationFinding,
    VerificationResult,
    VerificationScope,
    VerificationVerdict,
)


class ContactsTaskVerifier(BaseVerifier):
    """Verifies contacts-agent task results before they enter verified_facts."""

    name = "contacts_task_verifier"
    scope = VerificationScope.TASK_RESULT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        findings: list[VerificationFinding] = []

        result_text = (ctx.task_result or "").strip()

        # 1. Must have non-empty result
        if not result_text:
            findings.append(VerificationFinding(
                field="task_result",
                severity="error",
                message="Contacts task returned empty result.",
            ))

        # 2. Minimum meaningful length
        if result_text and len(result_text) < 10:
            findings.append(VerificationFinding(
                field="task_result",
                severity="warning",
                message=f"Contacts task result is suspiciously short ({len(result_text)} chars).",
                actual=result_text,
            ))

        # 3. Error signal detection
        error_patterns = ["无法", "失败", "error", "exception", "找不到"]
        has_error_signal = any(p in result_text.lower() for p in error_patterns)
        if has_error_signal and len(result_text) < 50:
            findings.append(VerificationFinding(
                field="task_result",
                severity="error",
                message="Contacts task result appears to be an error message rather than a real result.",
                actual=result_text[:200],
            ))

        errors = [f for f in findings if f.severity == "error"]
        if errors:
            return self._fail(
                VerificationVerdict.NEEDS_REPLAN,
                f"Contacts task verification failed with {len(errors)} error(s).",
                findings,
            )
        return self._pass("Contacts task result verified.", findings=findings)
