from __future__ import annotations

from src.verification.base import (
    BaseVerifier,
    NoOpVerifier,
    VerificationContext,
    VerificationFinding,
    VerificationResult,
    VerificationScope,
    VerificationVerdict,
)
from src.verification.runtime import build_verification_feedback, check_retry_budget


class DummyVerifier(BaseVerifier):
    name = "dummy_verifier"
    scope = VerificationScope.TASK_RESULT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        return self._pass(
            "dummy pass",
            findings=[
                VerificationFinding(
                    field="task_result",
                    severity="warning",
                    message="warning retained on pass",
                )
            ],
            metadata={"source": ctx.scope.value},
        )


class FailingVerifier(BaseVerifier):
    name = "failing_verifier"
    scope = VerificationScope.WORKFLOW_RESULT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        return self._fail(
            VerificationVerdict.HARD_FAIL,
            "verification failed",
            findings=[
                VerificationFinding(
                    field="final_result",
                    severity="error",
                    message="missing summary",
                )
            ],
        )


def test_verification_verdict_enum_is_frozen_to_three_values():
    assert {item.value for item in VerificationVerdict} == {
        "passed",
        "needs_replan",
        "hard_fail",
    }


def test_verification_context_supports_task_workflow_and_artifact_scopes():
    task_ctx = VerificationContext(scope=VerificationScope.TASK_RESULT, task_id="t-1")
    workflow_ctx = VerificationContext(scope=VerificationScope.WORKFLOW_RESULT, workflow_kind="default")
    artifact_ctx = VerificationContext(scope=VerificationScope.ARTIFACT, artifacts=["a.txt"])

    assert task_ctx.scope == VerificationScope.TASK_RESULT
    assert workflow_ctx.scope == VerificationScope.WORKFLOW_RESULT
    assert artifact_ctx.scope == VerificationScope.ARTIFACT


def test_base_verifier_pass_preserves_findings_and_metadata():
    result = DummyVerifier().verify(VerificationContext(scope=VerificationScope.TASK_RESULT))

    assert result.verdict == VerificationVerdict.PASSED
    assert result.report.verdict == VerificationVerdict.PASSED
    assert result.report.findings[0].severity == "warning"
    assert result.report.metadata == {"source": "task_result"}


def test_base_verifier_fail_preserves_findings():
    result = FailingVerifier().verify(VerificationContext(scope=VerificationScope.WORKFLOW_RESULT))

    assert result.verdict == VerificationVerdict.HARD_FAIL
    assert result.report.summary == "verification failed"
    assert result.report.findings[0].field == "final_result"


def test_build_verification_feedback_maps_needs_replan_to_replan():
    result = VerificationResult(
        verdict=VerificationVerdict.NEEDS_REPLAN,
        report=FailingVerifier()._fail(
            VerificationVerdict.NEEDS_REPLAN,
            "missing required fields",
            findings=[
                VerificationFinding(
                    field="task_result",
                    severity="error",
                    message="result is empty",
                )
            ],
        ).report,
    )

    feedback = build_verification_feedback(result, VerificationScope.TASK_RESULT, "task-1")

    assert feedback["source_scope"] == VerificationScope.TASK_RESULT
    assert feedback["source_target"] == "task-1"
    assert feedback["recommended_action"] == "replan"
    assert feedback["findings"][0]["message"] == "result is empty"


def test_build_verification_feedback_maps_hard_fail_to_fail():
    result = FailingVerifier().verify(VerificationContext(scope=VerificationScope.WORKFLOW_RESULT))

    feedback = build_verification_feedback(result, VerificationScope.WORKFLOW_RESULT, "workflow")

    assert feedback["verdict"] == VerificationVerdict.HARD_FAIL
    assert feedback["recommended_action"] == "fail"


def test_check_retry_budget_honors_phase4_boundary():
    assert check_retry_budget(0) is True
    assert check_retry_budget(3) is True
    assert check_retry_budget(4) is False


def test_noop_verifier_returns_default_pass_report():
    result = NoOpVerifier().verify(VerificationContext(scope=VerificationScope.TASK_RESULT))

    assert result.verdict == VerificationVerdict.PASSED
    assert result.report.verifier_name == "noop_verifier"
    assert "default pass" in result.report.summary.lower()
