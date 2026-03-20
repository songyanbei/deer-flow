"""Unit tests for the observability module."""

import json
import logging
import sys
import threading
from unittest.mock import patch

import pytest

# ── Tracer ──

from src.observability.tracer import SpanHandle, _NoopTracer, _sanitize_attributes, span


class TestSanitizeAttributes:
    def test_strips_none(self):
        result = _sanitize_attributes({"a": 1, "b": None, "c": "x"})
        assert result == {"a": 1, "c": "x"}

    def test_truncates_long_strings(self):
        long_str = "x" * 600
        result = _sanitize_attributes({"key": long_str})
        assert len(result["key"]) == 503  # 500 + "..."

    def test_converts_complex_to_string(self):
        result = _sanitize_attributes({"key": [1, 2, 3]})
        assert result["key"] == "[1, 2, 3]"

    def test_empty_input(self):
        assert _sanitize_attributes(None) == {}
        assert _sanitize_attributes({}) == {}


class TestSpanHandle:
    def test_elapsed_ms(self):
        handle = SpanHandle()
        import time
        time.sleep(0.01)
        assert handle.elapsed_ms > 5

    def test_noop_methods_do_not_raise(self):
        handle = SpanHandle()  # no otel_span
        handle.set_attribute("k", "v")
        handle.add_event("evt", {"a": 1})
        handle.record_error(RuntimeError("test"))


class TestSpanContextManager:
    def test_basic_span(self):
        with span("test_op", attributes={"run_id": "r1"}) as s:
            assert isinstance(s, SpanHandle)
            assert s.elapsed_ms >= 0

    def test_span_records_error(self):
        with pytest.raises(ValueError, match="boom"):
            with span("failing") as s:
                raise ValueError("boom")


class TestNoopTracer:
    def test_context_manager(self):
        tracer = _NoopTracer()
        with tracer.start_as_current_span("test"):
            pass


# ── Metrics ──

from src.observability.metrics import WorkflowMetrics, _histogram_stats, _labels_key


def _make_fallback_metrics() -> WorkflowMetrics:
    """Create a WorkflowMetrics instance forced into fallback (in-memory) mode."""
    WorkflowMetrics.reset()
    m = WorkflowMetrics.get()
    # Force fallback mode for testing snapshot
    m._use_otel = False
    m._otel_counters = {}
    m._otel_histograms = {}
    return m


class TestWorkflowMetrics:
    def setup_method(self):
        WorkflowMetrics.reset()

    def test_singleton(self):
        m1 = WorkflowMetrics.get()
        m2 = WorkflowMetrics.get()
        assert m1 is m2

    def test_record_llm_call(self):
        m = _make_fallback_metrics()
        m.record_llm_call("gpt-4", "planner", 120.5, 100, 50)
        snap = m.snapshot()
        assert any("llm.call.total" in k for k in snap["counters"])
        assert any("llm.call.duration_ms" in k for k in snap["histograms"])

    def test_record_workflow(self):
        m = _make_fallback_metrics()
        m.record_workflow_start("run1", "workflow")
        m.record_workflow_end("run1", "workflow", 5000.0, "DONE")
        snap = m.snapshot()
        assert any("workflow.total" in k for k in snap["counters"])
        assert any("workflow.duration_ms" in k for k in snap["histograms"])

    def test_record_task(self):
        m = _make_fallback_metrics()
        m.record_task("t1", "contacts-agent", "DONE", 1500.0)
        m.record_task("t2", "contacts-agent", "FAILED", 200.0)
        snap = m.snapshot()
        assert any("task.failure.total" in k for k in snap["counters"])

    def test_record_mcp_call(self):
        m = _make_fallback_metrics()
        m.record_mcp_call("search", "agent1", 50.0, True)
        snap = m.snapshot()
        assert any("mcp.call.total" in k for k in snap["counters"])

    def test_record_intervention(self):
        m = _make_fallback_metrics()
        m.record_intervention("agent1", "tool1", "high")
        snap = m.snapshot()
        assert any("intervention.total" in k for k in snap["counters"])

    def test_record_helper_retry(self):
        m = _make_fallback_metrics()
        m.record_helper_retry("parent1", "agent1")
        snap = m.snapshot()
        assert any("helper.retry.total" in k for k in snap["counters"])

    def test_snapshot_empty(self):
        m = _make_fallback_metrics()
        snap = m.snapshot()
        assert snap == {"counters": {}, "histograms": {}}

    def test_thread_safety(self):
        m = _make_fallback_metrics()
        errors = []

        def worker():
            try:
                for _ in range(100):
                    m.record_llm_call("model", "node", 10.0, 5, 5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        snap = m.snapshot()
        total_key = [k for k in snap["counters"] if "llm.call.total" in k][0]
        assert snap["counters"][total_key] == 400


class TestHistogramStats:
    def test_empty(self):
        result = _histogram_stats([])
        assert result == {"count": 0, "avg": 0, "max": 0, "p95": 0}

    def test_single(self):
        result = _histogram_stats([42.0])
        assert result["count"] == 1
        assert result["avg"] == 42.0
        assert result["max"] == 42.0
        assert result["p95"] == 42.0

    def test_multiple(self):
        result = _histogram_stats([10.0, 20.0, 30.0, 40.0, 50.0])
        assert result["count"] == 5
        assert result["avg"] == 30.0
        assert result["max"] == 50.0
        assert result["p95"] == 50.0


# ── Decision Log ──

from src.observability.decision_log import _truncate_dict, record_decision


class TestTruncateDict:
    def test_truncates_long_string(self):
        result = _truncate_dict({"key": "x" * 600})
        assert len(result["key"]) == 503  # 500 + "..."

    def test_recursive(self):
        result = _truncate_dict({"nested": {"key": "y" * 600}})
        assert len(result["nested"]["key"]) == 503

    def test_preserves_short(self):
        result = _truncate_dict({"key": "short"})
        assert result == {"key": "short"}

    def test_list(self):
        result = _truncate_dict(["a" * 600, "b"])
        assert len(result[0]) == 503
        assert result[1] == "b"


class TestRecordDecision:
    def test_outputs_json_line(self, caplog):
        decision_logger = logging.getLogger("deer-flow.decisions")
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True  # allow caplog to capture

        with caplog.at_level(logging.INFO, logger="deer-flow.decisions"):
            record_decision(
                "agent_route",
                run_id="run1",
                task_id="task-001",
                agent_name="contacts-agent",
                inputs={"task_desc": "test task"},
                output={"selected": "contacts-agent"},
                reason="fast_path_pre_assigned",
            )

        json_records = [r for r in caplog.records if r.name == "deer-flow.decisions"]
        assert len(json_records) >= 1
        entry = json.loads(json_records[-1].message)
        assert entry["decision_type"] == "agent_route"
        assert entry["run_id"] == "run1"
        assert entry["task_id"] == "task-001"
        assert "ts" in entry
        assert entry["reason"] == "fast_path_pre_assigned"

    def test_strips_none_fields(self, caplog):
        decision_logger = logging.getLogger("deer-flow.decisions")
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True

        with caplog.at_level(logging.INFO, logger="deer-flow.decisions"):
            record_decision("orchestration_mode")

        json_records = [r for r in caplog.records if r.name == "deer-flow.decisions"]
        entry = json.loads(json_records[-1].message)
        assert "run_id" not in entry
        assert "task_id" not in entry
        assert "agent_name" not in entry
        assert entry["decision_type"] == "orchestration_mode"


# ── Setup ──

from src.observability.setup import _setup_decision_logger, init_observability


class TestSetup:
    def test_init_observability_succeeds(self, caplog):
        with caplog.at_level(logging.INFO):
            init_observability()
        assert any("Initialization complete" in r.message for r in caplog.records)

    def test_decision_logger_no_propagate(self):
        _setup_decision_logger()
        decision_logger = logging.getLogger("deer-flow.decisions")
        assert decision_logger.propagate is False
        assert len(decision_logger.handlers) >= 1


# ── Config ──

from src.config.observability_config import ObservabilityConfig, get_observability_config, reset_observability_config


class TestObservabilityConfig:
    def setup_method(self):
        reset_observability_config()

    def test_defaults(self):
        config = get_observability_config()
        assert config.otel.enabled is False
        assert config.decision_log.enabled is True
        assert config.decision_log.output == "stdout"
        assert config.metrics_enabled is True

    def test_env_override(self):
        with patch.dict("os.environ", {"OTEL_ENABLED": "true", "OTEL_SERVICE_NAME": "test-svc"}):
            reset_observability_config()
            config = get_observability_config()
            assert config.otel.enabled is True
            assert config.otel.service_name == "test-svc"
