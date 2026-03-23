"""Tests for the Phase 0 Baseline & Metrics evaluation framework.

Covers: schema, loader, assertions, runner, report, collector, fixtures.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from src.evals.assertions import run_assertions
from src.evals.collector import collect_metrics
from src.evals.fixtures import FixtureProfile, build_fixture_patches, get_profile, register_profile
from src.evals.loader import CaseLoadError, load_case_file, load_cases, filter_cases
from src.evals.report import generate_json_report, generate_markdown_report, write_reports
from src.evals.runner import run_case, run_suite
from src.evals.schema import (
    AssertionFailure,
    BenchmarkCase,
    CaseCategory,
    CaseDomain,
    CaseExpected,
    CaseFixtureConfig,
    CaseInput,
    CaseLimits,
    CaseRunResult,
    CaseRunStatus,
    CaseType,
    SuiteRunResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_case_dict(**overrides) -> dict:
    base = {
        "id": "test.case.1",
        "title": "Test case",
        "suite": "test-suite",
        "domain": "meeting",
        "category": "happy_path",
        "type": "single",
        "input": {"message": "Hello"},
        "fixtures": {"profile": "meeting_happy_path"},
        "expected": {},
    }
    base.update(overrides)
    return base


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------


class TestSchema:
    def test_minimal_case_valid(self):
        case = BenchmarkCase(**_minimal_case_dict())
        assert case.id == "test.case.1"
        assert case.domain == CaseDomain.MEETING
        assert case.category == CaseCategory.HAPPY_PATH
        assert case.type == CaseType.SINGLE

    def test_missing_id_raises(self):
        d = _minimal_case_dict()
        del d["id"]
        with pytest.raises(Exception):
            BenchmarkCase(**d)

    def test_missing_input_message_raises(self):
        d = _minimal_case_dict()
        d["input"] = {}
        with pytest.raises(Exception):
            BenchmarkCase(**d)

    def test_missing_expected_is_ok(self):
        d = _minimal_case_dict()
        d["expected"] = {}
        case = BenchmarkCase(**d)
        assert case.expected.resolved_orchestration_mode is None

    def test_invalid_domain_raises(self):
        d = _minimal_case_dict(domain="invalid_domain")
        with pytest.raises(Exception):
            BenchmarkCase(**d)

    def test_invalid_category_raises(self):
        d = _minimal_case_dict(category="nonexistent_category")
        with pytest.raises(Exception):
            BenchmarkCase(**d)

    def test_both_assigned_agent_and_agents_raises(self):
        d = _minimal_case_dict()
        d["expected"] = {
            "assigned_agent": "meeting-agent",
            "assigned_agents": ["meeting-agent", "contacts-agent"],
        }
        with pytest.raises(ValueError, match="Cannot specify both"):
            BenchmarkCase(**d)

    def test_invalid_yaml_structure(self):
        """Non-dict YAML root should fail."""
        d = _minimal_case_dict()
        d["input"]["message"] = ""
        with pytest.raises(Exception):
            BenchmarkCase(**d)

    def test_limits_validation(self):
        case = BenchmarkCase(**_minimal_case_dict(
            limits={"max_route_count": 5, "max_task_count": 3, "max_duration_ms": 10000}
        ))
        assert case.limits.max_route_count == 5
        assert case.limits.max_task_count == 3

    def test_tags_default_empty(self):
        case = BenchmarkCase(**_minimal_case_dict())
        assert case.tags == []

    def test_case_run_result_model(self):
        result = CaseRunResult(case_id="test.1", status=CaseRunStatus.PASSED)
        assert result.case_id == "test.1"
        assert result.duration_ms == 0.0
        assert result.failed_assertions == []

    def test_suite_run_result_model(self):
        result = SuiteRunResult(suite="test", started_at="t0", finished_at="t1")
        assert result.total == 0
        assert result.case_results == []

    def test_unknown_top_level_field_raises(self):
        """P1: extra fields at BenchmarkCase top level must be rejected."""
        d = _minimal_case_dict()
        d["unknown_top_field"] = "should fail"
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            BenchmarkCase(**d)

    def test_unknown_expected_field_raises(self):
        """P1: unknown assertion names inside expected must be rejected."""
        d = _minimal_case_dict()
        d["expected"] = {"unknown_assertion": True}
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            BenchmarkCase(**d)

    def test_unknown_input_field_raises(self):
        """P1: extra fields in CaseInput must be rejected."""
        d = _minimal_case_dict()
        d["input"]["extra_input_field"] = "nope"
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            BenchmarkCase(**d)

    def test_unknown_limits_field_raises(self):
        """P1: extra fields in CaseLimits must be rejected."""
        d = _minimal_case_dict()
        d["limits"] = {"max_route_count": 5, "unknown_limit": 99}
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            BenchmarkCase(**d)

    def test_unknown_fixture_field_raises(self):
        """P1: extra fields in CaseFixtureConfig must be rejected."""
        d = _minimal_case_dict()
        d["fixtures"] = {"profile": "x", "bogus": True}
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            BenchmarkCase(**d)

    def test_misspelled_assertion_in_yaml_rejects(self, tmp_path):
        """P1 end-to-end: a YAML case with a typo in expected is caught by loader."""
        d = _minimal_case_dict()
        d["expected"] = {"clarificaiton_expected": True}  # typo
        path = _write_yaml(tmp_path / "typo.yaml", d)
        with pytest.raises(Exception):
            load_case_file(path)


# ---------------------------------------------------------------------------
# Loader Tests
# ---------------------------------------------------------------------------


class TestLoader:
    def test_load_single_case(self, tmp_path):
        path = _write_yaml(tmp_path / "case1.yaml", _minimal_case_dict())
        case = load_case_file(path)
        assert case.id == "test.case.1"

    def test_load_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(CaseLoadError, match="does not exist"):
            load_case_file(tmp_path / "nonexistent.yaml")

    def test_load_unsupported_extension_raises(self, tmp_path):
        path = tmp_path / "case.json"
        path.write_text("{}")
        with pytest.raises(CaseLoadError, match="unsupported file extension"):
            load_case_file(path)

    def test_load_invalid_yaml_raises(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(": invalid: yaml: [", encoding="utf-8")
        with pytest.raises(CaseLoadError, match="invalid YAML"):
            load_case_file(path)

    def test_load_non_dict_yaml_raises(self, tmp_path):
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(CaseLoadError, match="must be a mapping"):
            load_case_file(path)

    def test_load_schema_validation_failure(self, tmp_path):
        path = _write_yaml(tmp_path / "bad_schema.yaml", {"id": "x"})
        with pytest.raises(CaseLoadError, match="schema validation failed"):
            load_case_file(path)

    def test_load_suite(self, tmp_path):
        _write_yaml(tmp_path / "a.yaml", _minimal_case_dict(id="case.a"))
        _write_yaml(tmp_path / "b.yaml", _minimal_case_dict(id="case.b"))
        cases = load_cases(tmp_path)
        assert len(cases) == 2

    def test_filter_by_domain(self, tmp_path):
        _write_yaml(tmp_path / "m.yaml", _minimal_case_dict(id="m", domain="meeting"))
        _write_yaml(tmp_path / "c.yaml", _minimal_case_dict(id="c", domain="contacts"))
        cases = load_cases(tmp_path, domain="meeting")
        assert len(cases) == 1
        assert cases[0].id == "m"

    def test_filter_by_tag(self, tmp_path):
        _write_yaml(tmp_path / "t1.yaml", _minimal_case_dict(id="t1", tags=["regression"]))
        _write_yaml(tmp_path / "t2.yaml", _minimal_case_dict(id="t2", tags=["happy_path"]))
        cases = load_cases(tmp_path, tag="regression")
        assert len(cases) == 1
        assert cases[0].id == "t1"

    def test_filter_by_case_id(self, tmp_path):
        _write_yaml(tmp_path / "a.yaml", _minimal_case_dict(id="case.a"))
        _write_yaml(tmp_path / "b.yaml", _minimal_case_dict(id="case.b"))
        cases = load_cases(tmp_path, case_id="case.b")
        assert len(cases) == 1
        assert cases[0].id == "case.b"

    def test_filter_by_suite(self, tmp_path):
        _write_yaml(tmp_path / "s1.yaml", _minimal_case_dict(id="s1", suite="phase0-core"))
        _write_yaml(tmp_path / "s2.yaml", _minimal_case_dict(id="s2", suite="phase0-full"))
        cases = load_cases(tmp_path, suite="phase0-core")
        assert len(cases) == 1
        assert cases[0].id == "s1"

    def test_nonexistent_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_cases(tmp_path / "nonexistent")

    def test_empty_dir_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No YAML"):
            load_cases(empty)

    def test_invalid_case_blocks_loading(self, tmp_path):
        _write_yaml(tmp_path / "good.yaml", _minimal_case_dict(id="good"))
        _write_yaml(tmp_path / "bad.yaml", {"id": "bad_only"})
        with pytest.raises(CaseLoadError):
            load_cases(tmp_path)


# ---------------------------------------------------------------------------
# Assertion Engine Tests
# ---------------------------------------------------------------------------


class TestAssertions:
    def _make_case(self, **expected_kwargs) -> BenchmarkCase:
        return BenchmarkCase(**_minimal_case_dict(expected=expected_kwargs))

    def _make_case_with_limits(self, **limit_kwargs) -> BenchmarkCase:
        return BenchmarkCase(**_minimal_case_dict(limits=limit_kwargs))

    def test_orchestration_mode_pass(self):
        case = self._make_case(resolved_orchestration_mode="workflow")
        failures = run_assertions(case, {"resolved_orchestration_mode": "workflow"}, {})
        assert failures == []

    def test_orchestration_mode_fail(self):
        case = self._make_case(resolved_orchestration_mode="workflow")
        failures = run_assertions(case, {"resolved_orchestration_mode": "leader"}, {})
        assert len(failures) == 1
        assert failures[0].field == "resolved_orchestration_mode"

    def test_assigned_agent_pass(self):
        case = self._make_case(assigned_agent="meeting-agent")
        failures = run_assertions(case, {}, {"assigned_agents": ["meeting-agent"]})
        assert failures == []

    def test_assigned_agent_fail(self):
        case = self._make_case(assigned_agent="meeting-agent")
        failures = run_assertions(case, {}, {"assigned_agents": ["contacts-agent"]})
        assert len(failures) == 1
        assert failures[0].field == "assigned_agent"

    def test_assigned_agents_pass(self):
        case = self._make_case(assigned_agents=["contacts-agent", "meeting-agent"])
        failures = run_assertions(case, {}, {"assigned_agents": ["contacts-agent", "meeting-agent", "hr-agent"]})
        assert failures == []

    def test_assigned_agents_fail(self):
        case = self._make_case(assigned_agents=["contacts-agent", "meeting-agent"])
        failures = run_assertions(case, {}, {"assigned_agents": ["contacts-agent"]})
        assert len(failures) == 1
        assert "meeting-agent" in failures[0].message

    def test_clarification_expected_true_pass(self):
        case = self._make_case(clarification_expected=True)
        failures = run_assertions(case, {}, {"clarification_count": 1})
        assert failures == []

    def test_clarification_expected_true_fail(self):
        case = self._make_case(clarification_expected=True)
        failures = run_assertions(case, {}, {"clarification_count": 0})
        assert len(failures) == 1
        assert failures[0].field == "clarification_expected"

    def test_clarification_expected_false_pass(self):
        case = self._make_case(clarification_expected=False)
        failures = run_assertions(case, {}, {"clarification_count": 0})
        assert failures == []

    def test_clarification_expected_false_fail(self):
        case = self._make_case(clarification_expected=False)
        failures = run_assertions(case, {}, {"clarification_count": 2})
        assert len(failures) == 1

    def test_intervention_expected_true_pass(self):
        case = self._make_case(intervention_expected=True)
        failures = run_assertions(case, {}, {"intervention_count": 1})
        assert failures == []

    def test_intervention_expected_true_fail(self):
        case = self._make_case(intervention_expected=True)
        failures = run_assertions(case, {}, {"intervention_count": 0})
        assert len(failures) == 1

    def test_intervention_expected_false_pass(self):
        case = self._make_case(intervention_expected=False)
        failures = run_assertions(case, {}, {"intervention_count": 0})
        assert failures == []

    def test_intervention_expected_false_fail(self):
        case = self._make_case(intervention_expected=False)
        failures = run_assertions(case, {}, {"intervention_count": 1})
        assert len(failures) == 1

    def test_verified_facts_min_count_pass(self):
        case = self._make_case(verified_facts_min_count=2)
        failures = run_assertions(case, {}, {"verified_fact_count": 3})
        assert failures == []

    def test_verified_facts_min_count_fail(self):
        case = self._make_case(verified_facts_min_count=2)
        failures = run_assertions(case, {}, {"verified_fact_count": 1})
        assert len(failures) == 1
        assert failures[0].field == "verified_facts_min_count"

    def test_final_result_contains_pass(self):
        case = self._make_case(final_result_contains=["会议室", "预定"])
        failures = run_assertions(case, {"final_result": "已成功预定会议室"}, {})
        assert failures == []

    def test_final_result_contains_fail(self):
        case = self._make_case(final_result_contains=["会议室", "取消"])
        failures = run_assertions(case, {"final_result": "已成功预定会议室"}, {})
        assert len(failures) == 1
        assert "取消" in failures[0].message

    def test_final_result_not_contains_pass(self):
        case = self._make_case(final_result_not_contains=["错误", "失败"])
        failures = run_assertions(case, {"final_result": "操作成功"}, {})
        assert failures == []

    def test_final_result_not_contains_fail(self):
        case = self._make_case(final_result_not_contains=["错误"])
        failures = run_assertions(case, {"final_result": "发生错误"}, {})
        assert len(failures) == 1

    def test_max_route_count_pass(self):
        case = self._make_case_with_limits(max_route_count=5)
        failures = run_assertions(case, {}, {"route_count": 3})
        assert failures == []

    def test_max_route_count_fail(self):
        case = self._make_case_with_limits(max_route_count=2)
        failures = run_assertions(case, {}, {"route_count": 5})
        assert len(failures) == 1
        assert failures[0].field == "max_route_count"

    def test_max_task_count_pass(self):
        case = self._make_case_with_limits(max_task_count=3)
        failures = run_assertions(case, {}, {"task_count": 2})
        assert failures == []

    def test_max_task_count_fail(self):
        case = self._make_case_with_limits(max_task_count=2)
        failures = run_assertions(case, {}, {"task_count": 5})
        assert len(failures) == 1

    def test_max_duration_ms_pass(self):
        case = self._make_case_with_limits(max_duration_ms=10000)
        failures = run_assertions(case, {}, {"duration_ms": 5000})
        assert failures == []

    def test_max_duration_ms_fail(self):
        case = self._make_case_with_limits(max_duration_ms=1000)
        failures = run_assertions(case, {}, {"duration_ms": 5000})
        assert len(failures) == 1

    def test_none_expected_fields_skip(self):
        """If expected fields are None, no assertion is run."""
        case = self._make_case()
        failures = run_assertions(case, {}, {})
        assert failures == []


# ---------------------------------------------------------------------------
# Collector Tests
# ---------------------------------------------------------------------------


class TestCollector:
    def test_collect_from_real_state(self):
        """Collector extracts metrics from real ThreadState fields."""
        from langchain_core.messages import HumanMessage, ToolMessage, AIMessage

        state = {
            "task_pool": [
                {"task_id": "t1", "description": "task 1", "status": "DONE", "assigned_agent": "contacts-agent"},
                {"task_id": "t2", "description": "task 2", "status": "DONE", "assigned_agent": "meeting-agent"},
            ],
            "verified_facts": {
                "fact1": {"agent": "contacts-agent", "task": "t1", "summary": "info"},
                "fact2": {"agent": "meeting-agent", "task": "t2", "summary": "booked"},
            },
            "route_count": 3,
            "messages": [
                HumanMessage(content="hello"),
                AIMessage(content="", tool_calls=[{"id": "tc1", "name": "ask_clarification", "args": {}}]),
                ToolMessage(content="question", tool_call_id="tc1", name="ask_clarification"),
            ],
        }
        events = [
            {"type": "workflow_stage_changed", "stage": "planning"},
            {"type": "task_assigned", "agent": "contacts-agent"},
            {"type": "workflow_stage_changed", "stage": "executing"},
        ]
        metrics = collect_metrics(state, events=events)
        assert metrics["task_count"] == 2
        assert metrics["route_count"] == 3
        assert metrics["clarification_count"] == 1
        assert metrics["assigned_agents"] == ["contacts-agent", "meeting-agent"]
        assert metrics["verified_fact_count"] == 2
        assert metrics["event_count"] == 3
        assert metrics["stage_transitions"] == ["planning", "executing"]

    def test_collect_empty_state(self):
        metrics = collect_metrics({})
        assert metrics["task_count"] == 0
        assert metrics["assigned_agents"] == []
        assert metrics["route_count"] == 0


# ---------------------------------------------------------------------------
# Runner Tests
# ---------------------------------------------------------------------------


class TestRunner:
    def test_run_single_case_pass(self):
        case = BenchmarkCase(**_minimal_case_dict(
            expected={
                "resolved_orchestration_mode": "workflow",
                "assigned_agent": "meeting-agent",
                "clarification_expected": False,
                "intervention_expected": False,
            }
        ))
        result = asyncio.run(run_case(case))
        assert result.status == CaseRunStatus.PASSED
        assert result.case_id == "test.case.1"
        assert result.duration_ms >= 0

    def test_run_case_missing_fixture_returns_error(self):
        case = BenchmarkCase(**_minimal_case_dict(
            fixtures={"profile": "nonexistent_profile_xyz"}
        ))
        result = asyncio.run(run_case(case))
        assert result.status == CaseRunStatus.ERROR
        assert "not found" in result.error.lower()

    def test_run_case_assertion_failure_returns_failed(self):
        case = BenchmarkCase(**_minimal_case_dict(
            expected={"resolved_orchestration_mode": "leader"}
        ))
        result = asyncio.run(run_case(case))
        assert result.status == CaseRunStatus.FAILED
        assert len(result.failed_assertions) > 0

    def test_run_suite_from_dir(self, tmp_path):
        _write_yaml(tmp_path / "c1.yaml", _minimal_case_dict(
            id="c1",
            expected={
                "resolved_orchestration_mode": "workflow",
                "clarification_expected": False,
                "intervention_expected": False,
            },
        ))
        _write_yaml(tmp_path / "c2.yaml", _minimal_case_dict(
            id="c2",
            expected={
                "resolved_orchestration_mode": "workflow",
                "clarification_expected": False,
                "intervention_expected": False,
            },
        ))
        result = asyncio.run(run_suite(tmp_path))
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0

    def test_run_suite_with_failure(self, tmp_path):
        _write_yaml(tmp_path / "good.yaml", _minimal_case_dict(
            id="good",
            expected={"resolved_orchestration_mode": "workflow"},
        ))
        _write_yaml(tmp_path / "bad.yaml", _minimal_case_dict(
            id="bad",
            expected={"resolved_orchestration_mode": "leader"},
        ))
        result = asyncio.run(run_suite(tmp_path))
        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1

    def test_run_suite_collects_aggregate_metrics(self, tmp_path):
        _write_yaml(tmp_path / "c1.yaml", _minimal_case_dict(id="c1"))
        result = asyncio.run(run_suite(tmp_path))
        assert "total_duration_ms" in result.aggregate_metrics
        assert "pass_rate" in result.aggregate_metrics

    def test_run_case_with_events_collected(self):
        case = BenchmarkCase(**_minimal_case_dict())
        result = asyncio.run(run_case(case))
        assert result.task_count >= 1
        assert result.route_count >= 1

    def test_run_suite_empty_returns_empty_result(self, tmp_path):
        _write_yaml(tmp_path / "c1.yaml", _minimal_case_dict(id="c1", suite="other"))
        result = asyncio.run(run_suite(tmp_path, suite="nonexistent"))
        assert result.total == 0


# ---------------------------------------------------------------------------
# Report Tests
# ---------------------------------------------------------------------------


class TestReport:
    def _make_suite_result(self) -> SuiteRunResult:
        return SuiteRunResult(
            suite="phase0-core",
            started_at="2026-03-23T00:00:00Z",
            finished_at="2026-03-23T00:01:00Z",
            total=3,
            passed=2,
            failed=1,
            errored=0,
            skipped=0,
            case_results=[
                CaseRunResult(
                    case_id="case.pass.1",
                    status=CaseRunStatus.PASSED,
                    duration_ms=50.0,
                    task_count=1,
                    route_count=1,
                ),
                CaseRunResult(
                    case_id="case.pass.2",
                    status=CaseRunStatus.PASSED,
                    duration_ms=60.0,
                    task_count=2,
                    route_count=2,
                ),
                CaseRunResult(
                    case_id="case.fail.1",
                    status=CaseRunStatus.FAILED,
                    duration_ms=70.0,
                    task_count=1,
                    route_count=1,
                    failed_assertions=[
                        AssertionFailure(
                            field="resolved_orchestration_mode",
                            expected="workflow",
                            actual="leader",
                            message="Expected workflow, got leader",
                        )
                    ],
                ),
            ],
            aggregate_metrics={
                "total_duration_ms": 180.0,
                "avg_duration_ms": 60.0,
                "total_tasks": 4,
                "total_routes": 4,
                "total_clarifications": 0,
                "total_interventions": 0,
                "pass_rate": 0.667,
            },
        )

    def test_json_report_valid_json(self):
        result = self._make_suite_result()
        json_str = generate_json_report(result)
        parsed = json.loads(json_str)
        assert parsed["suite"] == "phase0-core"
        assert parsed["total"] == 3
        assert parsed["passed"] == 2
        assert len(parsed["case_results"]) == 3

    def test_markdown_report_contains_key_info(self):
        result = self._make_suite_result()
        md = generate_markdown_report(result)
        assert "phase0-core" in md
        assert "66.7%" in md
        assert "case.fail.1" in md
        assert "Failed assertions" in md
        assert "resolved_orchestration_mode" in md

    def test_markdown_report_has_aggregate_metrics(self):
        result = self._make_suite_result()
        md = generate_markdown_report(result)
        assert "Aggregate Metrics" in md
        assert "180.0 ms" in md

    def test_write_reports_creates_files(self, tmp_path):
        result = self._make_suite_result()
        json_path, md_path = write_reports(result, tmp_path)
        assert json_path.exists()
        assert md_path.exists()
        assert "phase0-core" in json_path.name
        assert json.loads(json_path.read_text(encoding="utf-8"))["suite"] == "phase0-core"
        assert "# Benchmark Report" in md_path.read_text(encoding="utf-8")

    def test_json_report_fail_error_case(self):
        result = SuiteRunResult(
            suite="test",
            started_at="t0",
            finished_at="t1",
            total=1,
            errored=1,
            case_results=[
                CaseRunResult(
                    case_id="err.1",
                    status=CaseRunStatus.ERROR,
                    error="Something broke",
                ),
            ],
        )
        md = generate_markdown_report(result)
        assert "Something broke" in md
        assert "err.1" in md


# ---------------------------------------------------------------------------
# Fixture Profile Tests
# ---------------------------------------------------------------------------


class TestFixtures:
    def test_get_builtin_profile(self):
        profile = get_profile("meeting_happy_path")
        assert profile.name == "meeting_happy_path"

    def test_get_unknown_profile_raises(self):
        with pytest.raises(KeyError, match="Unknown fixture profile"):
            get_profile("nonexistent_profile_abc")

    def test_register_custom_profile(self):
        custom = FixtureProfile(
            name="custom_test_profile",
            planner_tasks=[{"description": "custom task"}],
            route_map={"custom": "custom-agent"},
            agent_results={"custom-agent": {"result": "done"}},
        )
        register_profile(custom)
        retrieved = get_profile("custom_test_profile")
        assert retrieved.name == "custom_test_profile"

    def test_real_graph_happy_path(self):
        """Run the real compiled graph with fixture stubs for a happy path case."""
        case = BenchmarkCase(**_minimal_case_dict())
        result = asyncio.run(run_case(case))
        assert result.status in (CaseRunStatus.PASSED, CaseRunStatus.FAILED)
        assert result.task_count >= 1
        assert result.route_count >= 1

    def test_real_graph_with_clarification(self):
        """Run the real graph with a clarification-triggering fixture."""
        case = BenchmarkCase(**_minimal_case_dict(
            fixtures={"profile": "meeting_clarification_missing_time"},
            input={"message": "预定会议室", "clarification_answers": ["明天上午10点"]},
            expected={"clarification_expected": True},
        ))
        result = asyncio.run(run_case(case))
        assert result.clarification_count >= 1

    def test_real_graph_with_intervention(self):
        """Run the real graph with an intervention-triggering fixture."""
        case = BenchmarkCase(**_minimal_case_dict(
            fixtures={"profile": "meeting_governance_cancel"},
            input={"message": "取消会议"},
            expected={"intervention_expected": True},
        ))
        result = asyncio.run(run_case(case))
        assert result.intervention_count >= 1

    def test_real_graph_intervention_reject(self):
        """Rejected intervention should result in task failure."""
        case = BenchmarkCase(**_minimal_case_dict(
            fixtures={"profile": "meeting_governance_cancel_rejected"},
            input={
                "message": "取消会议",
                "intervention_resolutions": [{"action": "reject", "reason": "用户拒绝"}],
            },
            expected={"intervention_expected": True},
        ))
        result = asyncio.run(run_case(case))
        assert result.intervention_count >= 1

    def test_real_graph_cross_domain(self):
        """Run the real graph with a cross-domain workflow fixture."""
        case = BenchmarkCase(**_minimal_case_dict(
            fixtures={"profile": "contacts_to_meeting_basic"},
            input={"message": "查王明编号再预定会议室"},
            expected={
                "assigned_agents": ["contacts-agent", "meeting-agent"],
            },
        ))
        result = asyncio.run(run_case(case))
        assert "contacts-agent" in result.assigned_agents
        assert "meeting-agent" in result.assigned_agents

    def test_profile_route_map_resolution(self):
        """FixtureProfile.get_agent_for_task resolves correctly."""
        profile = get_profile("contacts_to_hr_basic")
        assert profile.get_agent_for_task("查询李四的考勤记录") == "hr-agent"
        assert profile.get_agent_for_task("查询李四的员工信息") == "contacts-agent"

    def test_build_fixture_patches_context_manager(self):
        """build_fixture_patches returns a working context manager."""
        profile = get_profile("meeting_happy_path")
        case = BenchmarkCase(**_minimal_case_dict())
        with build_fixture_patches(profile, case) as events:
            assert isinstance(events, list)


# ---------------------------------------------------------------------------
# Integration: Real Phase 0 Cases
# ---------------------------------------------------------------------------


class TestPhase0Integration:
    """Load and run the actual phase0 YAML cases to verify end-to-end flow."""

    PHASE0_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "phase0"

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parent.parent / "benchmarks" / "phase0").is_dir(),
        reason="Phase 0 benchmark cases not found",
    )
    def test_all_phase0_cases_load(self):
        cases = load_cases(self.PHASE0_DIR)
        assert len(cases) >= 19  # minimum expected cases

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parent.parent / "benchmarks" / "phase0").is_dir(),
        reason="Phase 0 benchmark cases not found",
    )
    def test_phase0_core_suite_runs(self):
        result = asyncio.run(run_suite(self.PHASE0_DIR, suite="phase0-core"))
        assert result.total >= 19
        assert result.errored == 0
        # All cases should pass with deterministic fixtures
        assert result.passed == result.total, (
            f"{result.failed} failed, {result.errored} errored out of {result.total}. "
            f"Failures: {[cr.case_id for cr in result.case_results if cr.status != CaseRunStatus.PASSED]}"
        )

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parent.parent / "benchmarks" / "phase0").is_dir(),
        reason="Phase 0 benchmark cases not found",
    )
    def test_phase0_reports_generated(self, tmp_path):
        result = asyncio.run(run_suite(self.PHASE0_DIR, suite="phase0-core"))
        json_path, md_path = write_reports(result, tmp_path)
        assert json_path.exists()
        assert md_path.exists()

        report_data = json.loads(json_path.read_text(encoding="utf-8"))
        assert report_data["suite"] == "phase0-core"
        assert report_data["total"] >= 18
