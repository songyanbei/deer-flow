"""Benchmark runner - orchestrates case loading, execution, assertion, and result collection.

Runs each benchmark case through the *real* compiled workflow graph
(planner -> router -> executor) with fixture stubs replacing LLM/MCP/agent
external dependencies.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from .assertions import run_assertions
from .collector import collect_metrics
from .fixtures import build_fixture_patches, get_profile
from .loader import load_cases
from .schema import BenchmarkCase, CaseRunResult, CaseRunStatus, SuiteRunResult

logger = logging.getLogger(__name__)


async def run_case(case: BenchmarkCase) -> CaseRunResult:
    """Run a single benchmark case through the real compiled graph."""
    start_time = time.monotonic()

    try:
        # Load fixture profile
        try:
            profile = get_profile(case.fixtures.profile)
        except KeyError as e:
            return CaseRunResult(
                case_id=case.id,
                status=CaseRunStatus.ERROR,
                error=f"Fixture profile not found: {e}",
            )

        # Execute via real graph with fixture patches
        with build_fixture_patches(profile, case) as events:
            from src.agents.graph import build_multi_agent_graph_for_test

            graph = build_multi_agent_graph_for_test()
            initial_state = {
                "messages": [HumanMessage(content=case.input.message)],
                "resolved_orchestration_mode": "workflow",
            }
            final_state = await graph.ainvoke(initial_state)

        # Collect metrics from the real ThreadState
        duration_ms = (time.monotonic() - start_time) * 1000
        metrics = collect_metrics(final_state, events=events)

        # Run assertions
        failures = run_assertions(case, final_state, metrics)

        status = CaseRunStatus.PASSED if not failures else CaseRunStatus.FAILED

        return CaseRunResult(
            case_id=case.id,
            status=status,
            duration_ms=duration_ms,
            resolved_orchestration_mode=final_state.get("resolved_orchestration_mode"),
            assigned_agents=metrics.get("assigned_agents", []),
            task_count=metrics.get("task_count", 0),
            route_count=metrics.get("route_count", 0),
            clarification_count=metrics.get("clarification_count", 0),
            intervention_count=metrics.get("intervention_count", 0),
            verified_fact_count=metrics.get("verified_fact_count", 0),
            llm_metrics=metrics.get("llm_metrics", {}),
            failed_assertions=failures,
        )

    except Exception as e:
        duration_ms = (time.monotonic() - start_time) * 1000
        logger.exception("Error running case %s", case.id)
        return CaseRunResult(
            case_id=case.id,
            status=CaseRunStatus.ERROR,
            duration_ms=duration_ms,
            error=str(e),
        )


async def run_suite(
    base_dir: Path | None = None,
    *,
    suite: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
    case_id: str | None = None,
) -> SuiteRunResult:
    """Load and run a suite of benchmark cases."""
    started_at = datetime.now(timezone.utc).isoformat()

    cases = load_cases(base_dir, suite=suite, domain=domain, tag=tag, case_id=case_id)

    if not cases:
        return SuiteRunResult(
            suite=suite or "all",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    results: list[CaseRunResult] = []
    for case in cases:
        result = await run_case(case)
        results.append(result)
        logger.info("[%s] %s — %s", result.status.value.upper(), case.id, case.title)

    finished_at = datetime.now(timezone.utc).isoformat()

    passed = sum(1 for r in results if r.status == CaseRunStatus.PASSED)
    failed = sum(1 for r in results if r.status == CaseRunStatus.FAILED)
    errored = sum(1 for r in results if r.status == CaseRunStatus.ERROR)
    skipped = sum(1 for r in results if r.status == CaseRunStatus.SKIPPED)

    aggregate = _compute_aggregate_metrics(results)

    return SuiteRunResult(
        suite=suite or "all",
        started_at=started_at,
        finished_at=finished_at,
        total=len(results),
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        case_results=results,
        aggregate_metrics=aggregate,
    )


def _compute_aggregate_metrics(results: list[CaseRunResult]) -> dict[str, Any]:
    """Compute aggregate metrics across all case results."""
    if not results:
        return {}

    total_duration = sum(r.duration_ms for r in results)
    total_tasks = sum(r.task_count for r in results)
    total_routes = sum(r.route_count for r in results)
    total_clarifications = sum(r.clarification_count for r in results)
    total_interventions = sum(r.intervention_count for r in results)

    return {
        "total_duration_ms": total_duration,
        "avg_duration_ms": total_duration / len(results) if results else 0,
        "total_tasks": total_tasks,
        "total_routes": total_routes,
        "total_clarifications": total_clarifications,
        "total_interventions": total_interventions,
        "pass_rate": sum(1 for r in results if r.status == CaseRunStatus.PASSED) / len(results) if results else 0,
    }
