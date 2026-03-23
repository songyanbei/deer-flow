from __future__ import annotations

import asyncio
import json

from src.evals.collector import collect_metrics
from src.evals.report import generate_json_report, generate_markdown_report
from src.evals.runner import run_case
from src.evals.schema import (
    BenchmarkCase,
    CaseCategory,
    CaseDomain,
    CaseExpected,
    CaseFixtureConfig,
    CaseInput,
    CaseRunResult,
    CaseRunStatus,
    CaseType,
    SuiteRunResult,
)


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        id="verification.case.1",
        title="Verification happy path",
        suite="phase0-core",
        domain=CaseDomain.MEETING,
        category=CaseCategory.HAPPY_PATH,
        type=CaseType.SINGLE,
        input=CaseInput(message="Book a meeting room tomorrow morning."),
        fixtures=CaseFixtureConfig(profile="meeting_happy_path"),
        expected=CaseExpected(resolved_orchestration_mode="workflow"),
    )


def test_collect_metrics_includes_task_and_workflow_verification_reports():
    state = {
        "task_pool": [
            {
                "task_id": "t1",
                "status": "DONE",
                "assigned_agent": "meeting-agent",
                "verification_report": {"verifier_name": "meeting_task_verifier", "verdict": "passed"},
            }
        ],
        "workflow_verification_report": {"verifier_name": "default_workflow_verifier", "verdict": "passed"},
        "verified_facts": {"t1": {"summary": "done"}},
    }

    metrics = collect_metrics(state)

    assert len(metrics["verification_reports"]) == 2
    assert metrics["verification_reports"][0]["verifier_name"] == "meeting_task_verifier"
    assert metrics["verification_reports"][1]["verifier_name"] == "default_workflow_verifier"


def test_run_case_populates_verification_fields_from_real_graph():
    result = asyncio.run(run_case(_case()))

    assert result.status == CaseRunStatus.PASSED
    assert result.verification_status == "passed"
    assert result.verification_retry_count == 0
    assert any(report.get("verifier_name") == "default_workflow_verifier" for report in result.verification_reports)


def test_markdown_report_renders_verification_status_findings_and_retry_count():
    result = SuiteRunResult(
        suite="phase0-core",
        started_at="2026-03-23T00:00:00Z",
        finished_at="2026-03-23T00:01:00Z",
        total=1,
        passed=1,
        case_results=[
            CaseRunResult(
                case_id="verification.case.1",
                status=CaseRunStatus.PASSED,
                verification_status="needs_replan",
                verification_retry_count=2,
                verification_reports=[
                    {
                        "verifier_name": "default_workflow_verifier",
                        "scope": "workflow_result",
                        "verdict": "needs_replan",
                        "summary": "summary missing required details",
                        "findings": [{"field": "final_result", "severity": "error", "message": "summary too short"}],
                    }
                ],
            )
        ],
    )

    markdown = generate_markdown_report(result)

    assert "Verification Details" in markdown
    assert "needs_replan" in markdown
    assert "Retry count" in markdown
    assert "summary too short" in markdown


def test_json_report_includes_verification_fields():
    result = SuiteRunResult(
        suite="phase0-core",
        started_at="2026-03-23T00:00:00Z",
        finished_at="2026-03-23T00:01:00Z",
        total=1,
        passed=1,
        case_results=[
            CaseRunResult(
                case_id="verification.case.1",
                status=CaseRunStatus.PASSED,
                verification_status="passed",
                verification_retry_count=0,
                verification_reports=[{"verifier_name": "default_workflow_verifier", "verdict": "passed"}],
            )
        ],
    )

    payload = json.loads(generate_json_report(result))

    assert payload["case_results"][0]["verification_status"] == "passed"
    assert payload["case_results"][0]["verification_retry_count"] == 0
    assert payload["case_results"][0]["verification_reports"][0]["verifier_name"] == "default_workflow_verifier"
