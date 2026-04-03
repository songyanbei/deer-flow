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


# ══════════════════════════════════════════════════════════════════════════
# Multi-tenant observability — tenant_id / user_id dimension tests
# ══════════════════════════════════════════════════════════════════════════

import tempfile
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 1. decision_log — tenant_id / user_id appear in JSON output
# ---------------------------------------------------------------------------


class TestDecisionLogTenantUser:
    """record_decision() must include tenant_id and user_id in the emitted JSON."""

    def test_record_includes_tenant_and_user(self, caplog):
        decision_logger = logging.getLogger("deer-flow.decisions")
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True

        with caplog.at_level(logging.INFO, logger="deer-flow.decisions"):
            record_decision(
                "intervention_trigger",
                run_id="run-1",
                task_id="task-1",
                agent_name="contacts-agent",
                tenant_id="org-alpha",
                user_id="user-42",
            )

        json_records = [r for r in caplog.records if r.name == "deer-flow.decisions"]
        assert len(json_records) >= 1
        entry = json.loads(json_records[-1].message)
        assert entry["tenant_id"] == "org-alpha"
        assert entry["user_id"] == "user-42"
        assert entry["decision_type"] == "intervention_trigger"

    def test_record_omits_none_identity_fields(self, caplog):
        decision_logger = logging.getLogger("deer-flow.decisions")
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True

        with caplog.at_level(logging.INFO, logger="deer-flow.decisions"):
            record_decision("orchestration_mode", run_id="r-2")

        json_records = [r for r in caplog.records if r.name == "deer-flow.decisions"]
        entry = json.loads(json_records[-1].message)
        assert "tenant_id" not in entry
        assert "user_id" not in entry

    def test_tenant_only_no_user(self, caplog):
        decision_logger = logging.getLogger("deer-flow.decisions")
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True

        with caplog.at_level(logging.INFO, logger="deer-flow.decisions"):
            record_decision("agent_route", tenant_id="org-beta")

        json_records = [r for r in caplog.records if r.name == "deer-flow.decisions"]
        entry = json.loads(json_records[-1].message)
        assert entry["tenant_id"] == "org-beta"
        assert "user_id" not in entry

    def test_different_tenants_produce_separate_entries(self, caplog):
        decision_logger = logging.getLogger("deer-flow.decisions")
        decision_logger.setLevel(logging.INFO)
        decision_logger.propagate = True

        with caplog.at_level(logging.INFO, logger="deer-flow.decisions"):
            record_decision("task_decomposition", tenant_id="org-a", user_id="u1")
            record_decision("task_decomposition", tenant_id="org-b", user_id="u2")

        json_records = [r for r in caplog.records if r.name == "deer-flow.decisions"]
        entries = [json.loads(r.message) for r in json_records[-2:]]
        assert entries[0]["tenant_id"] == "org-a"
        assert entries[0]["user_id"] == "u1"
        assert entries[1]["tenant_id"] == "org-b"
        assert entries[1]["user_id"] == "u2"


# ---------------------------------------------------------------------------
# 2. WorkflowMetrics — record_intervention() counter labels
# ---------------------------------------------------------------------------


class TestMetricsInterventionTenantLabels:
    """record_intervention() must embed tenant_id/user_id in counter label keys."""

    def setup_method(self):
        WorkflowMetrics.reset()

    def test_intervention_counter_with_tenant_and_user(self):
        m = _make_fallback_metrics()
        m.record_intervention("contacts-agent", "delete_contact", "high", tenant_id="org-x", user_id="user-7")

        snap = m.snapshot()
        matching = [k for k in snap["counters"] if "tenant_id=org-x" in k and "user_id=user-7" in k]
        assert len(matching) == 1
        assert snap["counters"][matching[0]] == 1

    def test_intervention_counter_without_identity(self):
        m = _make_fallback_metrics()
        m.record_intervention("hr-agent", "update_salary", "medium")

        snap = m.snapshot()
        matching = [k for k in snap["counters"] if "intervention.total" in k]
        assert len(matching) == 1
        key = matching[0]
        assert "tenant_id" not in key
        assert "user_id" not in key

    def test_separate_counters_per_tenant(self):
        m = _make_fallback_metrics()
        m.record_intervention("a", "tool_a", "low", tenant_id="t1")
        m.record_intervention("a", "tool_a", "low", tenant_id="t1")
        m.record_intervention("a", "tool_a", "low", tenant_id="t2")

        snap = m.snapshot()
        t1_keys = [k for k in snap["counters"] if "tenant_id=t1" in k]
        t2_keys = [k for k in snap["counters"] if "tenant_id=t2" in k]
        assert len(t1_keys) == 1
        assert snap["counters"][t1_keys[0]] == 2
        assert len(t2_keys) == 1
        assert snap["counters"][t2_keys[0]] == 1

    def test_user_dimension_independent(self):
        m = _make_fallback_metrics()
        m.record_intervention("a", "t", "low", tenant_id="org", user_id="alice")
        m.record_intervention("a", "t", "low", tenant_id="org", user_id="bob")

        snap = m.snapshot()
        alice_keys = [k for k in snap["counters"] if "user_id=alice" in k]
        bob_keys = [k for k in snap["counters"] if "user_id=bob" in k]
        assert len(alice_keys) == 1
        assert len(bob_keys) == 1
        assert snap["counters"][alice_keys[0]] == 1
        assert snap["counters"][bob_keys[0]] == 1


# ---------------------------------------------------------------------------
# 3. GovernanceLedger — record / query tenant & user filtering
# ---------------------------------------------------------------------------

from src.agents.governance.ledger import GovernanceLedger


class TestLedgerTenantUser:
    """Ledger entries must carry tenant_id/user_id and be queryable by them."""

    @pytest.fixture()
    def ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield GovernanceLedger(data_dir=tmp)

    def test_record_stores_tenant_and_user(self, ledger):
        entry = ledger.record(
            thread_id="th-1", run_id="r-1", task_id="t-1",
            source_agent="agent-a", hook_name="before_tool",
            source_path="test", risk_level="medium",
            category="tool_execution", decision="allow",
            tenant_id="org-1", user_id="user-a",
        )
        assert entry["tenant_id"] == "org-1"
        assert entry["user_id"] == "user-a"

    def test_record_defaults_tenant_to_default_when_none(self, ledger):
        entry = ledger.record(
            thread_id="th-2", run_id="r-2", task_id="t-2",
            source_agent="agent-b", hook_name="before_tool",
            source_path="test", risk_level="low",
            category="tool_execution", decision="allow",
        )
        assert entry["tenant_id"] == "default"
        assert entry["user_id"] is None

    def test_query_filters_by_tenant(self, ledger):
        for tid in ("org-a", "org-a", "org-b"):
            ledger.record(
                thread_id="th-x", run_id="r-x", task_id="t-x",
                source_agent="a", hook_name="h", source_path="p",
                risk_level="medium", category="c", decision="allow",
                tenant_id=tid,
            )
        results = ledger.query(tenant_id="org-a")
        assert len(results) == 2
        assert all(e["tenant_id"] == "org-a" for e in results)

    def test_query_filters_by_user(self, ledger):
        for uid in ("u1", "u1", "u2"):
            ledger.record(
                thread_id="th-x", run_id="r-x", task_id="t-x",
                source_agent="a", hook_name="h", source_path="p",
                risk_level="medium", category="c", decision="allow",
                tenant_id="org-x", user_id=uid,
            )
        results = ledger.query(user_id="u1")
        assert len(results) == 2
        assert all(e["user_id"] == "u1" for e in results)

    def test_query_filters_by_tenant_and_user_combined(self, ledger):
        ledger.record(
            thread_id="th-1", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="medium", category="c", decision="allow",
            tenant_id="org-a", user_id="u1",
        )
        ledger.record(
            thread_id="th-2", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="medium", category="c", decision="allow",
            tenant_id="org-a", user_id="u2",
        )
        ledger.record(
            thread_id="th-3", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="medium", category="c", decision="allow",
            tenant_id="org-b", user_id="u1",
        )
        results = ledger.query(tenant_id="org-a", user_id="u1")
        assert len(results) == 1
        assert results[0]["thread_id"] == "th-1"

    def test_pending_count_respects_tenant(self, ledger):
        ledger.record(
            thread_id="th-1", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="high", category="c", decision="require_intervention",
            tenant_id="org-a",
        )
        ledger.record(
            thread_id="th-2", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="high", category="c", decision="require_intervention",
            tenant_id="org-b",
        )
        assert ledger.pending_count(tenant_id="org-a") == 1
        assert ledger.pending_count(tenant_id="org-b") == 1
        assert ledger.pending_count() == 2

    def test_cross_tenant_query_returns_empty(self, ledger):
        """user-b exists only in tenant-b; querying tenant-a for user-b must return empty."""
        ledger.record(
            thread_id="th-1", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="medium", category="c", decision="allow",
            tenant_id="org-a", user_id="user-a",
        )
        ledger.record(
            thread_id="th-2", run_id="r", task_id="t",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="medium", category="c", decision="allow",
            tenant_id="org-b", user_id="user-b",
        )
        results = ledger.query(tenant_id="org-a", user_id="user-b")
        assert len(results) == 0

    def test_no_tenant_filter_returns_all(self, ledger):
        for tid in ("org-a", "org-b", "org-c"):
            ledger.record(
                thread_id="th", run_id="r", task_id="t",
                source_agent="a", hook_name="h", source_path="p",
                risk_level="medium", category="c", decision="allow",
                tenant_id=tid,
            )
        results = ledger.query(limit=0)
        assert len(results) == 3

    def test_ledger_persists_tenant_user_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger1 = GovernanceLedger(data_dir=tmp)
            ledger1.record(
                thread_id="th-p", run_id="r", task_id="t",
                source_agent="a", hook_name="h", source_path="p",
                risk_level="medium", category="c", decision="allow",
                tenant_id="org-persist", user_id="user-persist",
            )

            # Reload from disk
            ledger2 = GovernanceLedger(data_dir=tmp)
            results = ledger2.query(tenant_id="org-persist")
            assert len(results) == 1
            assert results[0]["user_id"] == "user-persist"


# ---------------------------------------------------------------------------
# 4. GovernanceEngine — tenant_id / user_id propagation to ledger
# ---------------------------------------------------------------------------

from src.agents.governance.engine import GovernanceEngine
from src.agents.governance.policy import PolicyRegistry


class TestEngineIdentityPropagation:
    """Engine entry points must forward tenant_id and user_id to the ledger."""

    @pytest.fixture()
    def engine_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = GovernanceLedger(data_dir=tmp)
            registry = PolicyRegistry()
            engine = GovernanceEngine(registry=registry, ledger=ledger)
            yield engine, ledger

    def test_evaluate_before_tool_records_tenant_user_on_policy_match(self, engine_and_ledger):
        engine, ledger = engine_and_ledger
        from src.agents.governance.types import GovernanceDecision
        engine._registry.add_rule({
            "rule_id": "r1",
            "match": {"tool": "delete_user"},
            "decision": GovernanceDecision.ALLOW.value,
            "risk_level": "low",
        })

        evaluation = engine.evaluate_before_tool(
            tool_name="delete_user", tool_args={},
            agent_name="hr-agent", task_id="t-1",
            run_id="r-1", thread_id="th-1",
            tenant_id="org-engine", user_id="user-engine",
        )
        assert evaluation.policy_matched is True

        entries = ledger.query(tenant_id="org-engine")
        assert len(entries) == 1
        assert entries[0]["user_id"] == "user-engine"

    def test_no_policy_match_produces_no_ledger_entry(self, engine_and_ledger):
        engine, ledger = engine_and_ledger
        evaluation = engine.evaluate_before_tool(
            tool_name="get_contacts", tool_args={},
            agent_name="contacts-agent", task_id="t-5",
            run_id="r-5", thread_id="th-5",
            tenant_id="org-noop", user_id="user-noop",
        )
        assert evaluation.policy_matched is False
        assert ledger.total_count == 0

    def test_record_interrupt_emit_propagates_identity(self, engine_and_ledger):
        engine, ledger = engine_and_ledger
        gov_id = engine.record_interrupt_emit(
            thread_id="th-2", run_id="r-2", task_id="t-2",
            source_agent="contacts-agent",
            interrupt_type="before_tool",
            source_path="middleware.intervention",
            tenant_id="org-emit", user_id="user-emit",
        )
        entry = ledger.get_by_id(gov_id)
        assert entry["tenant_id"] == "org-emit"
        assert entry["user_id"] == "user-emit"

    def test_record_interrupt_resolve_propagates_identity(self, engine_and_ledger):
        engine, ledger = engine_and_ledger
        gov_id = engine.record_interrupt_resolve(
            thread_id="th-3", run_id="r-3", task_id="t-3",
            source_agent="system",
            source_path="gateway.resolve",
            action_key="approve",
            resolution_behavior="resume_current_task",
            tenant_id="org-resolve", user_id="user-resolve",
        )
        entry = ledger.get_by_id(gov_id)
        assert entry["tenant_id"] == "org-resolve"
        assert entry["user_id"] == "user-resolve"

    def test_record_state_commit_audit_propagates_identity(self, engine_and_ledger):
        engine, ledger = engine_and_ledger
        gov_id = engine.record_state_commit_audit(
            thread_id="th-4", run_id="r-4",
            source_path="executor",
            commit_type="task_pool",
            tenant_id="org-commit", user_id="user-commit",
        )
        entry = ledger.get_by_id(gov_id)
        assert entry["tenant_id"] == "org-commit"
        assert entry["user_id"] == "user-commit"


# ---------------------------------------------------------------------------
# 5. Audit hooks — tenant_id + user_id extraction from metadata
# ---------------------------------------------------------------------------

from src.agents.governance.audit_hooks import (
    GovernanceInterruptEmitAuditHook,
    GovernanceInterruptResolveAuditHook,
    GovernanceStateCommitAuditHook,
)
from src.agents.hooks.base import RuntimeHookContext, RuntimeHookName


class TestAuditHooksUserIdPropagation:
    """All 3 audit hooks must extract user_id from metadata and pass to engine."""

    def test_interrupt_emit_hook_passes_tenant_and_user(self):
        mock_engine = MagicMock()
        hook = GovernanceInterruptEmitAuditHook(engine=mock_engine)

        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            thread_id="th-h1", run_id="r-h1",
            proposed_update={"task_pool": []},
            metadata={
                "task_id": "t-h1", "agent_name": "agent-h1",
                "interrupt_type": "before_tool", "source_path": "middleware",
                "tenant_id": "org-hook", "user_id": "user-hook-emit",
            },
        )
        hook.handle(ctx)

        mock_engine.record_interrupt_emit.assert_called_once()
        kw = mock_engine.record_interrupt_emit.call_args.kwargs
        assert kw["tenant_id"] == "org-hook"
        assert kw["user_id"] == "user-hook-emit"

    def test_interrupt_resolve_hook_passes_tenant_and_user(self):
        mock_engine = MagicMock()
        hook = GovernanceInterruptResolveAuditHook(engine=mock_engine)

        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="executor",
            thread_id="th-h2", run_id="r-h2",
            proposed_update={},
            metadata={
                "task_id": "t-h2", "source_path": "gateway.resolve",
                "action_key": "approve", "resolution_behavior": "resume_current_task",
                "request_id": "req-h2",
                "tenant_id": "org-hook-r", "user_id": "user-hook-resolve",
            },
        )
        hook.handle(ctx)

        mock_engine.record_interrupt_resolve.assert_called_once()
        kw = mock_engine.record_interrupt_resolve.call_args.kwargs
        assert kw["tenant_id"] == "org-hook-r"
        assert kw["user_id"] == "user-hook-resolve"

    def test_state_commit_hook_passes_tenant_and_user(self):
        mock_engine = MagicMock()
        hook = GovernanceStateCommitAuditHook(engine=mock_engine)

        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="executor",
            thread_id="th-h3", run_id="r-h3",
            proposed_update={},
            metadata={
                "source_path": "executor",
                "tenant_id": "org-hook-c", "user_id": "user-hook-commit",
            },
        )
        hook.handle(ctx)

        mock_engine.record_state_commit_audit.assert_called_once()
        kw = mock_engine.record_state_commit_audit.call_args.kwargs
        assert kw["tenant_id"] == "org-hook-c"
        assert kw["user_id"] == "user-hook-commit"

    def test_emit_hook_passes_none_when_user_id_absent(self):
        mock_engine = MagicMock()
        hook = GovernanceInterruptEmitAuditHook(engine=mock_engine)

        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            thread_id="th-h4", run_id="r-h4",
            proposed_update={"task_pool": []},
            metadata={
                "task_id": "t-h4", "agent_name": "a",
                "interrupt_type": "before_tool", "source_path": "test",
                "tenant_id": "org-nouser",
            },
        )
        hook.handle(ctx)

        kw = mock_engine.record_interrupt_emit.call_args.kwargs
        assert kw["tenant_id"] == "org-nouser"
        assert kw["user_id"] is None

    def test_resolve_hook_passes_none_when_user_id_absent(self):
        mock_engine = MagicMock()
        hook = GovernanceInterruptResolveAuditHook(engine=mock_engine)

        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="executor",
            thread_id="th-h5", run_id="r-h5",
            proposed_update={},
            metadata={
                "task_id": "t-h5", "source_path": "test",
                "tenant_id": "org-nouser-r",
            },
        )
        hook.handle(ctx)

        kw = mock_engine.record_interrupt_resolve.call_args.kwargs
        assert kw["tenant_id"] == "org-nouser-r"
        assert kw["user_id"] is None

    def test_commit_hook_passes_none_when_user_id_absent(self):
        mock_engine = MagicMock()
        hook = GovernanceStateCommitAuditHook(engine=mock_engine)

        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="executor",
            thread_id="th-h6", run_id="r-h6",
            proposed_update={},
            metadata={"source_path": "test", "tenant_id": "org-nouser-c"},
        )
        hook.handle(ctx)

        kw = mock_engine.record_state_commit_audit.call_args.kwargs
        assert kw["tenant_id"] == "org-nouser-c"
        assert kw["user_id"] is None


# ---------------------------------------------------------------------------
# 6. InterventionMiddleware — user_id forwarded to engine.evaluate_before_tool
# ---------------------------------------------------------------------------

from src.agents.middlewares.intervention_middleware import InterventionMiddleware


class TestInterventionMiddlewareUserIdForwarding:
    """InterventionMiddleware must pass user_id to the governance engine."""

    def test_evaluate_before_tool_receives_user_id(self):
        mock_engine = MagicMock()
        mock_eval = MagicMock()
        mock_eval.policy_matched = False
        mock_engine.evaluate_before_tool.return_value = mock_eval

        mw = InterventionMiddleware(
            run_id="run-mw", task_id="task-mw", agent_name="agent-mw",
            thread_id="th-mw", engine=mock_engine,
            tenant_id="org-mw", user_id="user-mw",
        )

        mock_request = MagicMock()
        mock_request.tool_call = {"name": "get_info", "args": {}, "id": "tc-1"}
        mock_handler = MagicMock(return_value="tool_result")

        mw.wrap_tool_call(mock_request, mock_handler)

        mock_engine.evaluate_before_tool.assert_called_once()
        kw = mock_engine.evaluate_before_tool.call_args.kwargs
        assert kw["tenant_id"] == "org-mw"
        assert kw["user_id"] == "user-mw"

    def test_middleware_without_user_id_passes_none(self):
        mock_engine = MagicMock()
        mock_eval = MagicMock()
        mock_eval.policy_matched = False
        mock_engine.evaluate_before_tool.return_value = mock_eval

        mw = InterventionMiddleware(
            run_id="run-mw2", task_id="task-mw2", agent_name="agent-mw2",
            thread_id="th-mw2", engine=mock_engine, tenant_id="org-mw2",
        )

        mock_request = MagicMock()
        mock_request.tool_call = {"name": "list_items", "args": {}, "id": "tc-2"}
        mock_handler = MagicMock(return_value="result")

        mw.wrap_tool_call(mock_request, mock_handler)

        kw = mock_engine.evaluate_before_tool.call_args.kwargs
        assert kw["user_id"] is None
