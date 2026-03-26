"""Tests for governance decision engine — unified entry point for all governance decisions."""

import tempfile

from src.agents.governance.engine import GovernanceEngine
from src.agents.governance.ledger import GovernanceLedger
from src.agents.governance.policy import PolicyRegistry
from src.agents.governance.types import GovernanceDecision, PolicyRule, RiskLevel


def _make_rule(**overrides) -> PolicyRule:
    defaults: PolicyRule = {
        "rule_id": "test_rule",
        "risk_level": "high",
        "decision": "require_intervention",
    }
    defaults.update(overrides)
    return defaults


class TestGovernanceEngine:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.registry = PolicyRegistry()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)
        self.engine = GovernanceEngine(registry=self.registry, ledger=self.ledger)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # --- evaluate_before_tool ---

    def test_no_policy_returns_no_match(self):
        result = self.engine.evaluate_before_tool(
            tool_name="create_event",
            tool_args={"title": "Meeting"},
            agent_name="meeting-agent",
            task_id="t1",
            run_id="r1",
            thread_id="th1",
        )
        assert result.policy_matched is False
        assert result.decision == GovernanceDecision.ALLOW
        # No ledger entry for no-match (fallback behavior)
        assert self.ledger.total_count == 0

    def test_policy_allow(self):
        self.registry.load([_make_rule(rule_id="allow_reads", tool="get_events", decision="allow", risk_level="medium")])
        result = self.engine.evaluate_before_tool(
            tool_name="get_events",
            tool_args={},
            agent_name="agent",
            task_id="t1",
            run_id="r1",
            thread_id="th1",
        )
        assert result.policy_matched is True
        assert result.decision == GovernanceDecision.ALLOW
        assert result.risk_level == RiskLevel.MEDIUM
        assert result.governance_id is not None
        # Allow decisions are recorded in ledger
        assert self.ledger.total_count == 1
        entry = self.ledger.get_by_id(result.governance_id)
        assert entry["status"] == "decided"

    def test_policy_deny(self):
        self.registry.load([_make_rule(rule_id="deny_delete", tool="delete_all", decision="deny", risk_level="critical")])
        result = self.engine.evaluate_before_tool(
            tool_name="delete_all",
            tool_args={},
            agent_name="agent",
            task_id="t1",
            run_id="r1",
            thread_id="th1",
        )
        assert result.policy_matched is True
        assert result.decision == GovernanceDecision.DENY
        assert result.risk_level == RiskLevel.CRITICAL
        # Deny recorded in ledger
        entry = self.ledger.get_by_id(result.governance_id)
        assert entry["decision"] == "deny"
        assert entry["status"] == "decided"

    def test_policy_require_intervention(self):
        self.registry.load([_make_rule(
            rule_id="risky_tool",
            tool="cancel_meeting",
            decision="require_intervention",
            risk_level="high",
            reason="Cancellation requires approval",
            title="Meeting Cancellation",
        )])
        result = self.engine.evaluate_before_tool(
            tool_name="cancel_meeting",
            tool_args={"meeting_id": "123"},
            agent_name="meeting-agent",
            task_id="t1",
            run_id="r1",
            thread_id="th1",
        )
        assert result.policy_matched is True
        assert result.decision == GovernanceDecision.REQUIRE_INTERVENTION
        assert result.risk_level == RiskLevel.HIGH
        assert result.reason == "Cancellation requires approval"
        assert result.title == "Meeting Cancellation"
        # require_intervention is NOT recorded here — the audit hook on
        # BEFORE_INTERRUPT_EMIT creates the ledger entry to avoid duplicates
        assert result.governance_id is None
        assert self.ledger.total_count == 0

    def test_ledger_fields_populated(self):
        # Use allow decision to verify ledger fields (require_intervention
        # defers ledger recording to the audit hook)
        self.registry.load([_make_rule(rule_id="r1", tool="create_event", decision="allow")])
        result = self.engine.evaluate_before_tool(
            tool_name="create_event",
            tool_args={"title": "standup", "time": "10:00"},
            agent_name="meeting-agent",
            task_id="task_42",
            run_id="run_7",
            thread_id="thread_99",
        )
        entry = self.ledger.get_by_id(result.governance_id)
        assert entry["thread_id"] == "thread_99"
        assert entry["run_id"] == "run_7"
        assert entry["task_id"] == "task_42"
        assert entry["source_agent"] == "meeting-agent"
        assert entry["hook_name"] == "before_tool"
        assert entry["category"] == "tool_execution"
        assert entry["metadata"]["tool_name"] == "create_event"
        assert "title" in entry["metadata"]["tool_args_keys"]

    # --- record_interrupt_emit ---

    def test_record_interrupt_emit(self):
        gov_id = self.engine.record_interrupt_emit(
            thread_id="th1",
            run_id="r1",
            task_id="t1",
            source_agent="meeting-agent",
            interrupt_type="intervention",
            source_path="executor.request_intervention",
            risk_level=RiskLevel.HIGH,
            request_id="intv_abc",
        )
        assert gov_id is not None
        entry = self.ledger.get_by_id(gov_id)
        assert entry["decision"] == "require_intervention"
        assert entry["status"] == "pending_intervention"
        assert entry["hook_name"] == "before_interrupt_emit"

    # --- record_interrupt_resolve ---

    def test_record_interrupt_resolve_updates_existing(self):
        # First emit
        self.engine.record_interrupt_emit(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="agent", interrupt_type="intervention",
            source_path="executor", request_id="intv_xyz",
        )
        assert self.ledger.pending_count() == 1

        # Then resolve
        gov_id = self.engine.record_interrupt_resolve(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="system",
            source_path="gateway.resolve_intervention",
            request_id="intv_xyz",
            action_key="approve",
            resolved_by="operator",
        )
        assert gov_id is not None
        entry = self.ledger.get_by_id(gov_id)
        assert entry["status"] == "resolved"
        assert entry["resolved_by"] == "operator"
        assert self.ledger.pending_count() == 0

    def test_record_interrupt_resolve_reject(self):
        self.engine.record_interrupt_emit(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="agent", interrupt_type="intervention",
            source_path="executor", request_id="intv_rej",
        )
        self.engine.record_interrupt_resolve(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="system", source_path="gateway",
            request_id="intv_rej", action_key="reject",
        )
        entry = self.ledger.get_by_request_id("intv_rej")
        assert entry["status"] == "rejected"

    def test_record_interrupt_resolve_no_existing(self):
        gov_id = self.engine.record_interrupt_resolve(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="system", source_path="router",
            request_id="unknown",
        )
        assert gov_id is not None
        # Creates a new entry with continue_after_resolution
        entry = self.ledger.get_by_id(gov_id)
        assert entry["decision"] == "continue_after_resolution"

    # --- record_state_commit_audit ---

    def test_record_state_commit_audit(self):
        gov_id = self.engine.record_state_commit_audit(
            thread_id="th1",
            run_id="r1",
            source_path="node_wrapper.after_hooks",
            commit_type="task_pool",
        )
        entry = self.ledger.get_by_id(gov_id)
        assert entry["hook_name"] == "before_task_pool_commit"
        assert entry["decision"] == "allow"
        assert entry["status"] == "decided"
