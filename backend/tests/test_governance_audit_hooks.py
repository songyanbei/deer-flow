"""Tests for governance audit hook handlers."""

import tempfile

from src.agents.governance.audit_hooks import (
    GovernanceInterruptEmitAuditHook,
    GovernanceInterruptResolveAuditHook,
    GovernanceStateCommitAuditHook,
    install_governance_audit_hooks,
)
from src.agents.governance.engine import GovernanceEngine
from src.agents.governance.ledger import GovernanceLedger
from src.agents.governance.policy import PolicyRegistry
from src.agents.hooks.base import RuntimeHookContext, RuntimeHookName
from src.agents.hooks.registry import RuntimeHookRegistry, runtime_hook_registry


class TestGovernanceInterruptEmitAuditHook:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.registry = PolicyRegistry()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)
        self.engine = GovernanceEngine(registry=self.registry, ledger=self.ledger)
        self.hook = GovernanceInterruptEmitAuditHook(engine=self.engine)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_records_audit_entry(self):
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            run_id="run-1",
            thread_id="thread-1",
            state={},
            proposed_update={
                "task_pool": [{
                    "task_id": "t1",
                    "intervention_request": {
                        "request_id": "intv_test",
                        "risk_level": "high",
                    },
                }],
            },
            metadata={
                "interrupt_type": "intervention",
                "task_id": "t1",
                "agent_name": "meeting-agent",
                "source_path": "executor.request_intervention",
            },
        )
        result = self.hook.handle(ctx)
        assert result.decision.value == "continue"
        assert self.ledger.total_count == 1
        entry = self.ledger.query()[0]
        assert entry["hook_name"] == "before_interrupt_emit"
        assert entry["source_agent"] == "meeting-agent"
        assert entry["request_id"] == "intv_test"

    def test_handles_missing_metadata_gracefully(self):
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="unknown",
            proposed_update={},
            metadata={},
        )
        result = self.hook.handle(ctx)
        assert result.decision.value == "continue"
        # Should still record, just with empty/default fields
        assert self.ledger.total_count == 1


class TestGovernanceInterruptResolveAuditHook:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.registry = PolicyRegistry()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)
        self.engine = GovernanceEngine(registry=self.registry, ledger=self.ledger)
        self.hook = GovernanceInterruptResolveAuditHook(engine=self.engine)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_records_resolve_audit(self):
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="gateway",
            run_id="run-1",
            thread_id="thread-1",
            proposed_update={},
            metadata={
                "task_id": "t1",
                "source_path": "gateway.resolve_intervention",
                "action_key": "approve",
                "resolution_behavior": "resume_current_task",
                "request_id": "intv_test",
            },
        )
        result = self.hook.handle(ctx)
        assert result.decision.value == "continue"
        assert self.ledger.total_count == 1

    def test_resolve_updates_pending_entry(self):
        # Simulate: emit created a pending entry
        self.engine.record_interrupt_emit(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="agent", interrupt_type="intervention",
            source_path="executor", request_id="intv_pending",
        )
        assert self.ledger.pending_count() == 1

        # Resolve hook fires
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="gateway",
            run_id="r1",
            thread_id="th1",
            proposed_update={},
            metadata={
                "task_id": "t1",
                "source_path": "gateway.resolve_intervention",
                "action_key": "approve",
                "request_id": "intv_pending",
            },
        )
        self.hook.handle(ctx)
        assert self.ledger.pending_count() == 0
        entry = self.ledger.get_by_request_id("intv_pending")
        assert entry["status"] == "resolved"

    def test_inline_vs_operator_resolved_by(self):
        # Gateway source → operator
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="gateway",
            proposed_update={},
            metadata={
                "source_path": "gateway.resolve_intervention",
                "action_key": "approve",
            },
        )
        self.hook.handle(ctx)
        self.ledger.query()[0]
        # New entry since no pending exists — check metadata
        assert self.ledger.total_count == 1

        # Router source → inline
        ctx2 = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="router",
            proposed_update={},
            metadata={
                "source_path": "router.in_graph_resolve",
                "action_key": "approve",
            },
        )
        self.hook.handle(ctx2)
        assert self.ledger.total_count == 2

    def test_governance_operator_resolve_detected_as_operator(self):
        """Governance operator console resolve must set resolved_by=operator."""
        # Seed a pending entry first
        self.engine.record_interrupt_emit(
            thread_id="th1", run_id="r1", task_id="t1",
            source_agent="agent", interrupt_type="intervention",
            source_path="executor", request_id="intv_gov_op",
        )
        assert self.ledger.pending_count() == 1

        # Resolve via governance.operator_resolve source path
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="governance",
            proposed_update={},
            metadata={
                "source_path": "governance.operator_resolve",
                "action_key": "approve",
                "request_id": "intv_gov_op",
            },
        )
        self.hook.handle(ctx)
        assert self.ledger.pending_count() == 0
        entry = self.ledger.get_by_request_id("intv_gov_op")
        assert entry["status"] == "resolved"
        assert entry["resolved_by"] == "operator"


class TestGovernanceStateCommitAuditHook:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.registry = PolicyRegistry()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)
        self.engine = GovernanceEngine(registry=self.registry, ledger=self.ledger)
        self.hook = GovernanceStateCommitAuditHook(engine=self.engine)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_records_task_pool_commit_audit(self):
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="gateway",
            run_id="run-1",
            thread_id="thread-1",
            proposed_update={"task_pool": [{"task_id": "t1"}]},
            metadata={
                "source_path": "gateway.resolve_intervention",
                "task_pool_size": 1,
                "commit_reason": "state_commit",
            },
        )
        result = self.hook.handle(ctx)
        assert result.decision.value == "continue"
        assert self.ledger.total_count == 1
        entry = self.ledger.query()[0]
        assert entry["hook_name"] == "before_task_pool_commit"
        assert entry["decision"] == "allow"
        assert entry["status"] == "decided"

    def test_records_verified_facts_commit_audit(self):
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT,
            node_name="executor",
            run_id="run-1",
            thread_id="thread-1",
            proposed_update={"verified_facts": {"task-1": {"summary": "done"}}},
            metadata={
                "source_path": "node_wrapper.executor",
                "facts_count": 1,
                "commit_reason": "state_commit",
            },
        )
        self.hook.handle(ctx)
        assert self.ledger.total_count == 1
        entry = self.ledger.query()[0]
        assert entry["hook_name"] == "before_verified_facts_commit"
        assert entry["source_path"] == "node_wrapper.executor"


class TestInstallGovernanceAuditHooks:
    def teardown_method(self):
        runtime_hook_registry.clear()

    def test_installs_on_fresh_registry(self):
        reg = RuntimeHookRegistry()
        install_governance_audit_hooks(registry=reg)
        assert reg.has_handlers(RuntimeHookName.BEFORE_INTERRUPT_EMIT)
        assert reg.has_handlers(RuntimeHookName.AFTER_INTERRUPT_RESOLVE)
        assert reg.has_handlers(RuntimeHookName.BEFORE_TASK_POOL_COMMIT)
        assert reg.has_handlers(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT)

    def test_idempotent(self):
        reg = RuntimeHookRegistry()
        install_governance_audit_hooks(registry=reg)
        install_governance_audit_hooks(registry=reg)
        assert len(reg.get_handlers(RuntimeHookName.BEFORE_INTERRUPT_EMIT)) == 1
        assert len(reg.get_handlers(RuntimeHookName.AFTER_INTERRUPT_RESOLVE)) == 1
        assert len(reg.get_handlers(RuntimeHookName.BEFORE_TASK_POOL_COMMIT)) == 1
        assert len(reg.get_handlers(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT)) == 1

    def test_reinstalls_after_clear(self):
        install_governance_audit_hooks()
        assert runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_INTERRUPT_EMIT)
        assert runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_TASK_POOL_COMMIT)

        runtime_hook_registry.clear()
        assert not runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_INTERRUPT_EMIT)
        assert not runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_TASK_POOL_COMMIT)

        install_governance_audit_hooks()
        assert runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_INTERRUPT_EMIT)
        assert runtime_hook_registry.has_handlers(RuntimeHookName.AFTER_INTERRUPT_RESOLVE)
        assert runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_TASK_POOL_COMMIT)
        assert runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT)
