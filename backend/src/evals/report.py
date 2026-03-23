"""Report generator - produces JSON and Markdown reports from suite results."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .schema import CaseRunResult, CaseRunStatus, SuiteRunResult

logger = logging.getLogger(__name__)


def generate_json_report(result: SuiteRunResult) -> str:
    """Generate a detailed JSON report from a suite run result."""
    return result.model_dump_json(indent=2)


def generate_markdown_report(result: SuiteRunResult) -> str:
    """Generate a human-readable Markdown summary report."""
    lines: list[str] = []
    lines.append(f"# Benchmark Report: {result.suite}")
    lines.append("")
    lines.append(f"- **Started**: {result.started_at}")
    lines.append(f"- **Finished**: {result.finished_at}")
    lines.append(f"- **Total cases**: {result.total}")
    lines.append(f"- **Passed**: {result.passed}")
    lines.append(f"- **Failed**: {result.failed}")
    lines.append(f"- **Errors**: {result.errored}")
    lines.append(f"- **Skipped**: {result.skipped}")
    lines.append("")

    # Pass rate
    if result.total > 0:
        pass_rate = result.passed / result.total * 100
        lines.append(f"**Pass rate**: {pass_rate:.1f}%")
        lines.append("")

    # Aggregate metrics
    if result.aggregate_metrics:
        lines.append("## Aggregate Metrics")
        lines.append("")
        agg = result.aggregate_metrics
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total duration | {agg.get('total_duration_ms', 0):.1f} ms |")
        lines.append(f"| Avg duration | {agg.get('avg_duration_ms', 0):.1f} ms |")
        lines.append(f"| Total tasks | {agg.get('total_tasks', 0)} |")
        lines.append(f"| Total routes | {agg.get('total_routes', 0)} |")
        lines.append(f"| Total clarifications | {agg.get('total_clarifications', 0)} |")
        lines.append(f"| Total interventions | {agg.get('total_interventions', 0)} |")
        lines.append("")

    # Case results table
    lines.append("## Case Results")
    lines.append("")
    lines.append("| Case ID | Status | Duration | Tasks | Routes | Verification | Retries |")
    lines.append("|---------|--------|----------|-------|--------|--------------|---------|")

    for cr in result.case_results:
        status_icon = _status_icon(cr.status)
        v_status = cr.verification_status or "n/a"
        lines.append(
            f"| {cr.case_id} | {status_icon} {cr.status.value} | {cr.duration_ms:.1f}ms "
            f"| {cr.task_count} | {cr.route_count} | {v_status} | {cr.verification_retry_count} |"
        )
    lines.append("")

    # Verification details
    cases_with_verification = [cr for cr in result.case_results if cr.verification_reports]
    if cases_with_verification:
        lines.append("## Verification Details")
        lines.append("")
        for cr in cases_with_verification:
            lines.append(f"### {cr.case_id}")
            lines.append("")
            lines.append(f"- **Verification status**: {cr.verification_status or 'n/a'}")
            lines.append(f"- **Retry count**: {cr.verification_retry_count}")
            lines.append("")
            for vr in cr.verification_reports:
                lines.append(f"**{vr.get('verifier_name', '?')}** ({vr.get('scope', '?')}): {vr.get('verdict', '?')}")
                if vr.get("summary"):
                    lines.append(f"  Summary: {vr['summary']}")
                findings = vr.get("findings", [])
                if findings:
                    for f in findings:
                        lines.append(f"  - `{f.get('field', '?')}` [{f.get('severity', '?')}]: {f.get('message', '')}")
                lines.append("")

    # Failed / Error details
    failures = [cr for cr in result.case_results if cr.status in (CaseRunStatus.FAILED, CaseRunStatus.ERROR)]
    if failures:
        lines.append("## Failures & Errors")
        lines.append("")
        for cr in failures:
            lines.append(f"### {cr.case_id}")
            lines.append("")
            if cr.error:
                lines.append(f"**Error**: {cr.error}")
                lines.append("")
            if cr.failed_assertions:
                lines.append("**Failed assertions**:")
                lines.append("")
                for fa in cr.failed_assertions:
                    lines.append(f"- `{fa.field}`: {fa.message}")
                    lines.append(f"  - Expected: `{fa.expected}`")
                    lines.append(f"  - Actual: `{fa.actual}`")
                lines.append("")

    return "\n".join(lines)


def write_reports(result: SuiteRunResult, output_dir: Path) -> tuple[Path, Path]:
    """Write both JSON and Markdown reports to the output directory.

    Returns (json_path, markdown_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"report_{result.suite}.json"
    md_path = output_dir / f"report_{result.suite}.md"

    json_path.write_text(generate_json_report(result), encoding="utf-8")
    md_path.write_text(generate_markdown_report(result), encoding="utf-8")

    logger.info("Reports written to %s", output_dir)
    return json_path, md_path


def _status_icon(status: CaseRunStatus) -> str:
    return {
        CaseRunStatus.PASSED: "✅",
        CaseRunStatus.FAILED: "❌",
        CaseRunStatus.ERROR: "⚠️",
        CaseRunStatus.SKIPPED: "⏭️",
    }.get(status, "")
