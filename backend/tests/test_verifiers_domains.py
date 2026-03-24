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


def test_workflow_verifier_rejects_conflicting_terminal_states_for_same_agent():
    """If the task_pool has both DONE and FAILED for the same agent,
    the workflow verifier should reject with an error finding."""
    result = DefaultWorkflowVerifier().verify(
        VerificationContext(
            scope=VerificationScope.WORKFLOW_RESULT,
            final_result="考勤已查到，孙琦本月出勤20天。",
            task_pool=[
                {"task_id": "t-old", "status": "FAILED", "assigned_agent": "hr-agent"},
                {"task_id": "t-new", "status": "DONE", "assigned_agent": "hr-agent"},
                {"task_id": "t-contacts", "status": "DONE", "assigned_agent": "contacts-agent"},
            ],
            verified_facts={"t-new": {"summary": "考勤OK"}, "t-contacts": {"summary": "联系人OK"}},
        )
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    error_findings = [f for f in result.report.findings if f.severity == "error"]
    assert any("Conflicting terminal states" in f.message for f in error_findings)


def test_workflow_verifier_passes_when_different_agents_have_different_terminal_states():
    """DONE for one agent and FAILED for a different agent is acceptable
    (partial success), not a conflicting-state error."""
    result = DefaultWorkflowVerifier().verify(
        VerificationContext(
            scope=VerificationScope.WORKFLOW_RESULT,
            final_result="部分完成：联系人已查到，但HR查询失败。",
            task_pool=[
                {"task_id": "t-contacts", "status": "DONE", "assigned_agent": "contacts-agent"},
                {"task_id": "t-hr", "status": "FAILED", "assigned_agent": "hr-agent"},
            ],
            verified_facts={"t-contacts": {"summary": "联系人OK"}},
        )
    )

    # Should pass (with warning about failed task), not error about conflicting states
    assert result.verdict == VerificationVerdict.PASSED
    error_findings = [f for f in result.report.findings if f.severity == "error"]
    assert not any("Conflicting terminal states" in f.message for f in error_findings)


def test_artifact_validator_is_registered_for_artifact_scope():
    validator = GenericArtifactValidator()

    assert validator.scope == VerificationScope.ARTIFACT
