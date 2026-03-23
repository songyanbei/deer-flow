from __future__ import annotations

from src.verification.artifacts.generic import GenericArtifactValidator
from src.verification.base import VerificationContext, VerificationScope, VerificationVerdict
from src.verification.domains.contacts import ContactsTaskVerifier
from src.verification.domains.hr import HrTaskVerifier
from src.verification.domains.meeting import MeetingTaskVerifier
from src.verification.workflows.default import DefaultWorkflowVerifier


def test_meeting_verifier_passes_meaningful_result():
    result = MeetingTaskVerifier().verify(
        VerificationContext(
            scope=VerificationScope.TASK_RESULT,
            task_result="Booked the room and shared the calendar invite with attendees.",
        )
    )

    assert result.verdict == VerificationVerdict.PASSED
    assert result.report.findings == []


def test_meeting_verifier_rejects_empty_result():
    result = MeetingTaskVerifier().verify(
        VerificationContext(scope=VerificationScope.TASK_RESULT, task_result="")
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert result.report.findings[0].field == "task_result"


def test_contacts_verifier_rejects_short_error_like_result():
    result = ContactsTaskVerifier().verify(
        VerificationContext(scope=VerificationScope.TASK_RESULT, task_result="error")
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert any(f.severity == "error" for f in result.report.findings)


def test_hr_verifier_preserves_warning_findings_on_pass():
    result = HrTaskVerifier().verify(
        VerificationContext(scope=VerificationScope.TASK_RESULT, task_result="ok")
    )

    assert result.verdict == VerificationVerdict.PASSED
    assert len(result.report.findings) == 1
    assert result.report.findings[0].severity == "warning"


def test_workflow_verifier_passes_terminal_summary():
    result = DefaultWorkflowVerifier().verify(
        VerificationContext(
            scope=VerificationScope.WORKFLOW_RESULT,
            final_result="Employee A-1001 was identified and no leave conflicts were found for tomorrow.",
            task_pool=[
                {"task_id": "t1", "status": "DONE"},
                {"task_id": "t2", "status": "DONE"},
            ],
            verified_facts={"t1": {"summary": "Employee A-1001"}},
        )
    )

    assert result.verdict == VerificationVerdict.PASSED
    assert result.report.findings == []


def test_workflow_verifier_rejects_empty_summary():
    result = DefaultWorkflowVerifier().verify(
        VerificationContext(
            scope=VerificationScope.WORKFLOW_RESULT,
            final_result="",
            task_pool=[{"task_id": "t1", "status": "DONE"}],
            verified_facts={"t1": {"summary": "done"}},
        )
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert any(f.field == "final_result" for f in result.report.findings)


def test_workflow_verifier_rejects_non_terminal_tasks():
    result = DefaultWorkflowVerifier().verify(
        VerificationContext(
            scope=VerificationScope.WORKFLOW_RESULT,
            final_result="Summary is present and looks long enough for validation.",
            task_pool=[
                {"task_id": "t1", "status": "DONE"},
                {"task_id": "t2", "status": "RUNNING"},
            ],
            verified_facts={"t1": {"summary": "done"}},
        )
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert any(f.field == "task_pool" for f in result.report.findings)


def test_workflow_verifier_keeps_warning_findings_when_summary_passes_with_partial_failures():
    result = DefaultWorkflowVerifier().verify(
        VerificationContext(
            scope=VerificationScope.WORKFLOW_RESULT,
            final_result="The workflow finished, but one supporting lookup failed and the summary reflects that gap.",
            task_pool=[
                {"task_id": "t1", "status": "DONE"},
                {"task_id": "t2", "status": "FAILED"},
            ],
            verified_facts={"t1": {"summary": "completed"}},
        )
    )

    assert result.verdict == VerificationVerdict.PASSED
    assert len(result.report.findings) == 1
    assert result.report.findings[0].severity == "warning"


def test_artifact_validator_is_registered_for_artifact_scope():
    validator = GenericArtifactValidator()

    assert validator.scope == VerificationScope.ARTIFACT
