"""Assertion engine for benchmark case evaluation."""

from __future__ import annotations

import logging
from typing import Any

from .schema import AssertionFailure, BenchmarkCase, CaseExpected, CaseLimits

logger = logging.getLogger(__name__)


def run_assertions(case: BenchmarkCase, state: dict[str, Any], metrics: dict[str, Any]) -> list[AssertionFailure]:
    """Run all applicable assertions for a case against the execution state and metrics.

    Returns a list of assertion failures (empty if all pass).
    """
    failures: list[AssertionFailure] = []
    expected = case.expected

    # --- Orchestration mode ---
    if expected.resolved_orchestration_mode is not None:
        actual = state.get("resolved_orchestration_mode")
        if actual != expected.resolved_orchestration_mode:
            failures.append(AssertionFailure(
                field="resolved_orchestration_mode",
                expected=expected.resolved_orchestration_mode,
                actual=actual,
                message=f"Expected orchestration mode '{expected.resolved_orchestration_mode}', got '{actual}'",
            ))

    # --- Assigned agent (single) ---
    if expected.assigned_agent is not None:
        actual_agents = metrics.get("assigned_agents", [])
        if expected.assigned_agent not in actual_agents:
            failures.append(AssertionFailure(
                field="assigned_agent",
                expected=expected.assigned_agent,
                actual=actual_agents,
                message=f"Expected agent '{expected.assigned_agent}' in assigned agents, got {actual_agents}",
            ))

    # --- Assigned agents (multiple, order-insensitive) ---
    if expected.assigned_agents is not None:
        actual_agents = metrics.get("assigned_agents", [])
        expected_set = set(expected.assigned_agents)
        actual_set = set(actual_agents)
        if not expected_set.issubset(actual_set):
            missing = expected_set - actual_set
            failures.append(AssertionFailure(
                field="assigned_agents",
                expected=sorted(expected_set),
                actual=sorted(actual_set),
                message=f"Missing expected agents: {sorted(missing)}",
            ))

    # --- Clarification expected ---
    if expected.clarification_expected is not None:
        actual_count = metrics.get("clarification_count", 0)
        if expected.clarification_expected and actual_count == 0:
            failures.append(AssertionFailure(
                field="clarification_expected",
                expected=True,
                actual=False,
                message="Expected clarification to occur, but none happened",
            ))
        elif not expected.clarification_expected and actual_count > 0:
            failures.append(AssertionFailure(
                field="clarification_expected",
                expected=False,
                actual=True,
                message=f"Expected no clarification, but {actual_count} occurred",
            ))

    # --- Intervention expected ---
    if expected.intervention_expected is not None:
        actual_count = metrics.get("intervention_count", 0)
        if expected.intervention_expected and actual_count == 0:
            failures.append(AssertionFailure(
                field="intervention_expected",
                expected=True,
                actual=False,
                message="Expected intervention to occur, but none happened",
            ))
        elif not expected.intervention_expected and actual_count > 0:
            failures.append(AssertionFailure(
                field="intervention_expected",
                expected=False,
                actual=True,
                message=f"Expected no intervention, but {actual_count} occurred",
            ))

    # --- Verified facts min count ---
    if expected.verified_facts_min_count is not None:
        actual_count = metrics.get("verified_fact_count", 0)
        if actual_count < expected.verified_facts_min_count:
            failures.append(AssertionFailure(
                field="verified_facts_min_count",
                expected=expected.verified_facts_min_count,
                actual=actual_count,
                message=f"Expected at least {expected.verified_facts_min_count} verified facts, got {actual_count}",
            ))

    # --- Final result contains ---
    if expected.final_result_contains is not None:
        final_result = state.get("final_result") or ""
        for substring in expected.final_result_contains:
            if substring not in final_result:
                failures.append(AssertionFailure(
                    field="final_result_contains",
                    expected=substring,
                    actual=final_result[:200],
                    message=f"Expected final result to contain '{substring}'",
                ))

    # --- Final result not contains ---
    if expected.final_result_not_contains is not None:
        final_result = state.get("final_result") or ""
        for substring in expected.final_result_not_contains:
            if substring in final_result:
                failures.append(AssertionFailure(
                    field="final_result_not_contains",
                    expected=f"NOT '{substring}'",
                    actual=final_result[:200],
                    message=f"Expected final result to NOT contain '{substring}'",
                ))

    # --- Task statuses ---
    if expected.task_statuses is not None:
        task_pool = state.get("task_pool") or []
        for pattern, expected_status in expected.task_statuses.items():
            matched = [t for t in task_pool if pattern.lower() in t.get("description", "").lower()]
            if not matched:
                failures.append(AssertionFailure(
                    field="task_statuses",
                    expected=f"{pattern} -> {expected_status}",
                    actual="no matching task found",
                    message=f"No task matching pattern '{pattern}' found in task pool",
                ))
            else:
                for t in matched:
                    if t.get("status") != expected_status:
                        failures.append(AssertionFailure(
                            field="task_statuses",
                            expected=expected_status,
                            actual=t.get("status"),
                            message=f"Task '{t.get('description')}' expected status '{expected_status}', got '{t.get('status')}'",
                        ))

    # --- Limits ---
    if case.limits:
        failures.extend(_check_limits(case.limits, metrics))

    return failures


def _check_limits(limits: CaseLimits, metrics: dict[str, Any]) -> list[AssertionFailure]:
    """Check performance limits."""
    failures: list[AssertionFailure] = []

    if limits.max_route_count is not None:
        actual = metrics.get("route_count", 0)
        if actual > limits.max_route_count:
            failures.append(AssertionFailure(
                field="max_route_count",
                expected=f"<= {limits.max_route_count}",
                actual=actual,
                message=f"Route count {actual} exceeds limit {limits.max_route_count}",
            ))

    if limits.max_task_count is not None:
        actual = metrics.get("task_count", 0)
        if actual > limits.max_task_count:
            failures.append(AssertionFailure(
                field="max_task_count",
                expected=f"<= {limits.max_task_count}",
                actual=actual,
                message=f"Task count {actual} exceeds limit {limits.max_task_count}",
            ))

    if limits.max_duration_ms is not None:
        actual = metrics.get("duration_ms", 0)
        if actual > limits.max_duration_ms:
            failures.append(AssertionFailure(
                field="max_duration_ms",
                expected=f"<= {limits.max_duration_ms}",
                actual=actual,
                message=f"Duration {actual:.1f}ms exceeds limit {limits.max_duration_ms}ms",
            ))

    return failures
