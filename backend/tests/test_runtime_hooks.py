"""Comprehensive tests for the runtime hook harness MVP.

Covers:
1. Hook contract (base types)
2. Registry (register, priority, clear, list)
3. Runner (continue, short_circuit, error, empty registry)
4. Verification hook adapters (task + workflow)
5. After-node hook integration via node_wrapper
"""

from __future__ import annotations

import pytest

from src.agents.hooks.base import (
    HookDecision,
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookName,
    RuntimeHookResult,
)
from src.agents.hooks.registry import RuntimeHookRegistry
from src.agents.hooks.runner import HookExecutionError, run_runtime_hooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class PassthroughHook(RuntimeHookHandler):
    name = "passthrough"
    priority = 100

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        return RuntimeHookResult.ok()


class PatchHook(RuntimeHookHandler):
    """Adds a key to proposed_update."""

    def __init__(self, hook_name: str = "patch", priority: int = 100, patch: dict | None = None):
        self.name = hook_name
        self.priority = priority
        self._patch = patch or {}

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        return RuntimeHookResult.ok(patch=self._patch, reason=f"patched_by_{self.name}")


class ShortCircuitHook(RuntimeHookHandler):
    name = "short_circuit"
    priority = 50

    def __init__(self, patch: dict | None = None):
        self._patch = patch or {}

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        return RuntimeHookResult.short_circuit(patch=self._patch, reason="forced_short_circuit")


class ExplodingHook(RuntimeHookHandler):
    name = "exploding"
    priority = 100

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        raise ValueError("boom")


class AccumulatorHook(RuntimeHookHandler):
    """Records what it sees in proposed_update for assertion."""

    def __init__(self, hook_name: str = "accumulator", priority: int = 100):
        self.name = hook_name
        self.priority = priority
        self.seen_updates: list[dict] = []

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        self.seen_updates.append(dict(ctx.proposed_update))
        return RuntimeHookResult.ok()


# ===========================================================================
# 1. Hook contract unit tests
# ===========================================================================

class TestRuntimeHookName:
    def test_slice_a_hooks_exist(self):
        expected_slice_a = {"after_planner", "after_router", "after_executor", "after_task_complete", "before_final_result_commit"}
        expected_slice_b = {"before_interrupt_emit", "after_interrupt_resolve", "before_task_pool_commit", "before_verified_facts_commit"}
        actual = {h.value for h in RuntimeHookName}
        assert expected_slice_a | expected_slice_b == actual

    def test_enum_string_value(self):
        assert RuntimeHookName.AFTER_PLANNER == "after_planner"
        assert RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT == "before_final_result_commit"


class TestRuntimeHookContext:
    def test_defaults(self):
        ctx = RuntimeHookContext(hook_name=RuntimeHookName.AFTER_PLANNER, node_name="planner")
        assert ctx.hook_name == RuntimeHookName.AFTER_PLANNER
        assert ctx.node_name == "planner"
        assert ctx.state == {}
        assert ctx.proposed_update == {}
        assert ctx.metadata == {}
        assert ctx.run_id is None
        assert ctx.thread_id is None

    def test_proposed_update_is_a_copy(self):
        original = {"key": "value"}
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_EXECUTOR,
            node_name="executor",
            proposed_update=original,
        )
        ctx.proposed_update["key"] = "modified"
        assert original["key"] == "value"


class TestRuntimeHookResult:
    def test_ok_factory(self):
        result = RuntimeHookResult.ok(patch={"a": 1}, reason="test")
        assert result.decision == HookDecision.CONTINUE
        assert result.update_patch == {"a": 1}
        assert result.reason == "test"

    def test_short_circuit_factory(self):
        result = RuntimeHookResult.short_circuit(patch={"b": 2})
        assert result.decision == HookDecision.SHORT_CIRCUIT
        assert result.update_patch == {"b": 2}

    def test_defaults(self):
        result = RuntimeHookResult()
        assert result.decision == HookDecision.CONTINUE
        assert result.update_patch == {}
        assert result.reason is None


# ===========================================================================
# 2. Registry tests
# ===========================================================================

class TestRuntimeHookRegistry:
    def _fresh_registry(self) -> RuntimeHookRegistry:
        return RuntimeHookRegistry()

    def test_empty_registry(self):
        reg = self._fresh_registry()
        assert reg.get_handlers(RuntimeHookName.AFTER_PLANNER) == []
        assert not reg.has_handlers(RuntimeHookName.AFTER_PLANNER)

    def test_register_and_retrieve(self):
        reg = self._fresh_registry()
        h = PassthroughHook()
        reg.register(RuntimeHookName.AFTER_PLANNER, h)
        handlers = reg.get_handlers(RuntimeHookName.AFTER_PLANNER)
        assert len(handlers) == 1
        assert handlers[0] is h
        assert reg.has_handlers(RuntimeHookName.AFTER_PLANNER)

    def test_priority_ordering(self):
        reg = self._fresh_registry()
        h_low = PatchHook("low", priority=10)
        h_high = PatchHook("high", priority=200)
        h_mid = PatchHook("mid", priority=50)
        reg.register(RuntimeHookName.AFTER_EXECUTOR, h_high)
        reg.register(RuntimeHookName.AFTER_EXECUTOR, h_low)
        reg.register(RuntimeHookName.AFTER_EXECUTOR, h_mid)
        names = [h.name for h in reg.get_handlers(RuntimeHookName.AFTER_EXECUTOR)]
        assert names == ["low", "mid", "high"]

    def test_same_priority_insertion_order(self):
        reg = self._fresh_registry()
        h1 = PatchHook("first", priority=100)
        h2 = PatchHook("second", priority=100)
        h3 = PatchHook("third", priority=100)
        reg.register(RuntimeHookName.AFTER_ROUTER, h1)
        reg.register(RuntimeHookName.AFTER_ROUTER, h2)
        reg.register(RuntimeHookName.AFTER_ROUTER, h3)
        names = [h.name for h in reg.get_handlers(RuntimeHookName.AFTER_ROUTER)]
        assert names == ["first", "second", "third"]

    def test_priority_override(self):
        reg = self._fresh_registry()
        h = PatchHook("handler", priority=200)
        reg.register(RuntimeHookName.AFTER_PLANNER, h, priority=5)
        snapshot = reg.list_hooks()
        assert snapshot["after_planner"][0]["priority"] == 5

    def test_clear_specific_hook(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_PLANNER, PassthroughHook())
        reg.register(RuntimeHookName.AFTER_ROUTER, PassthroughHook())
        reg.clear(RuntimeHookName.AFTER_PLANNER)
        assert not reg.has_handlers(RuntimeHookName.AFTER_PLANNER)
        assert reg.has_handlers(RuntimeHookName.AFTER_ROUTER)

    def test_clear_all(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_PLANNER, PassthroughHook())
        reg.register(RuntimeHookName.AFTER_ROUTER, PassthroughHook())
        reg.clear()
        assert not reg.has_handlers(RuntimeHookName.AFTER_PLANNER)
        assert not reg.has_handlers(RuntimeHookName.AFTER_ROUTER)

    def test_list_hooks_snapshot(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_EXECUTOR, PatchHook("alpha", priority=10))
        reg.register(RuntimeHookName.AFTER_EXECUTOR, PatchHook("beta", priority=20))
        snapshot = reg.list_hooks()
        assert "after_executor" in snapshot
        assert len(snapshot["after_executor"]) == 2
        assert snapshot["after_executor"][0]["handler_name"] == "alpha"
        assert snapshot["after_executor"][1]["handler_name"] == "beta"

    def test_repr(self):
        reg = self._fresh_registry()
        assert "hooks=0" in repr(reg)
        reg.register(RuntimeHookName.AFTER_PLANNER, PassthroughHook())
        assert "hooks=1" in repr(reg)
        assert "handlers=1" in repr(reg)


# ===========================================================================
# 3. Runner tests
# ===========================================================================

class TestRunRuntimeHooks:
    def _fresh_registry(self) -> RuntimeHookRegistry:
        return RuntimeHookRegistry()

    def test_empty_registry_returns_unchanged(self):
        reg = self._fresh_registry()
        update = {"execution_state": "DONE", "final_result": "ok"}
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_PLANNER,
            node_name="planner",
            state={},
            proposed_update=update,
            registry=reg,
        )
        assert result == update

    def test_continue_patch_merge(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_PLANNER, PatchHook("h1", patch={"added_key": "value1"}))
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_PLANNER,
            node_name="planner",
            state={},
            proposed_update={"execution_state": "DONE"},
            registry=reg,
        )
        assert result["execution_state"] == "DONE"
        assert result["added_key"] == "value1"

    def test_multiple_handlers_accumulate_patches(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_PLANNER, PatchHook("h1", priority=10, patch={"a": 1}))
        reg.register(RuntimeHookName.AFTER_PLANNER, PatchHook("h2", priority=20, patch={"b": 2}))
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_PLANNER,
            node_name="planner",
            state={},
            proposed_update={"base": True},
            registry=reg,
        )
        assert result == {"base": True, "a": 1, "b": 2}

    def test_later_handler_sees_earlier_patch(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_PLANNER, PatchHook("first", priority=10, patch={"x": 42}))
        observer = AccumulatorHook("observer", priority=20)
        reg.register(RuntimeHookName.AFTER_PLANNER, observer)
        run_runtime_hooks(
            RuntimeHookName.AFTER_PLANNER,
            node_name="planner",
            state={},
            proposed_update={"original": True},
            registry=reg,
        )
        assert len(observer.seen_updates) == 1
        assert observer.seen_updates[0]["x"] == 42
        assert observer.seen_updates[0]["original"] is True

    def test_short_circuit_stops_chain(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_EXECUTOR, ShortCircuitHook(patch={"stopped": True}))
        never_reached = AccumulatorHook("never", priority=200)
        reg.register(RuntimeHookName.AFTER_EXECUTOR, never_reached)
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_EXECUTOR,
            node_name="executor",
            state={},
            proposed_update={"base": True},
            registry=reg,
        )
        assert result["stopped"] is True
        assert result["base"] is True
        assert len(never_reached.seen_updates) == 0

    def test_short_circuit_overwrites_same_key(self):
        reg = self._fresh_registry()
        reg.register(
            RuntimeHookName.AFTER_EXECUTOR,
            ShortCircuitHook(patch={"execution_state": "ERROR"}),
        )
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_EXECUTOR,
            node_name="executor",
            state={},
            proposed_update={"execution_state": "DONE"},
            registry=reg,
        )
        assert result["execution_state"] == "ERROR"

    def test_exception_raises_hook_execution_error(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_PLANNER, ExplodingHook())
        with pytest.raises(HookExecutionError) as exc_info:
            run_runtime_hooks(
                RuntimeHookName.AFTER_PLANNER,
                node_name="planner",
                state={},
                proposed_update={},
                registry=reg,
            )
        assert exc_info.value.hook_name == RuntimeHookName.AFTER_PLANNER
        assert exc_info.value.handler_name == "exploding"
        assert isinstance(exc_info.value.cause, ValueError)

    def test_exception_stops_chain(self):
        reg = self._fresh_registry()
        reg.register(RuntimeHookName.AFTER_ROUTER, ExplodingHook())
        observer = AccumulatorHook("after_explode", priority=200)
        reg.register(RuntimeHookName.AFTER_ROUTER, observer)
        with pytest.raises(HookExecutionError):
            run_runtime_hooks(
                RuntimeHookName.AFTER_ROUTER,
                node_name="router",
                state={},
                proposed_update={},
                registry=reg,
            )
        assert len(observer.seen_updates) == 0

    def test_metadata_passed_to_handler(self):
        reg = self._fresh_registry()

        class MetadataReader(RuntimeHookHandler):
            name = "meta_reader"
            priority = 100
            captured_meta: dict = {}

            def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
                self.captured_meta = dict(ctx.metadata)
                return RuntimeHookResult.ok()

        reader = MetadataReader()
        reg.register(RuntimeHookName.AFTER_TASK_COMPLETE, reader)
        run_runtime_hooks(
            RuntimeHookName.AFTER_TASK_COMPLETE,
            node_name="executor",
            state={},
            proposed_update={},
            metadata={"task_id": "abc", "agent": "test-agent"},
            registry=reg,
        )
        assert reader.captured_meta["task_id"] == "abc"
        assert reader.captured_meta["agent"] == "test-agent"


# ===========================================================================
# 4. Verification hook adapter tests
# ===========================================================================

class TestTaskVerificationHook:
    def test_passed(self):
        from src.agents.hooks.verification_hooks import TaskVerificationHook

        hook = TaskVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_TASK_COMPLETE,
            node_name="executor",
            metadata={
                "task": {"task_id": "t1", "description": "test task"},
                "assigned_agent": "test-agent",
                "task_result": "some result",
                "resolved_inputs": None,
                "verified_facts": {},
                "artifacts": [],
            },
        )
        result = hook.handle(ctx)
        assert result.decision == HookDecision.CONTINUE
        assert "task_verification_passed" in (result.reason or "")
        assert "_verification_result" in result.update_patch

    def test_hard_fail(self):
        from unittest.mock import patch as mock_patch

        from src.agents.hooks.verification_hooks import TaskVerificationHook
        from src.verification.base import (
            VerificationReport,
            VerificationResult,
            VerificationScope,
            VerificationVerdict,
        )

        fake_result = VerificationResult(
            verdict=VerificationVerdict.HARD_FAIL,
            report=VerificationReport(
                verifier_name="test",
                scope=VerificationScope.TASK_RESULT,
                verdict=VerificationVerdict.HARD_FAIL,
                summary="critical failure",
            ),
        )
        hook = TaskVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_TASK_COMPLETE,
            node_name="executor",
            metadata={
                "task": {"task_id": "t1", "description": "test"},
                "assigned_agent": "a",
                "task_result": "r",
                "verified_facts": {},
                "artifacts": [],
            },
        )
        with mock_patch("src.verification.runtime.run_task_verification", return_value=fake_result):
            result = hook.handle(ctx)
        assert result.decision == HookDecision.SHORT_CIRCUIT
        assert result.update_patch["execution_state"] == "ERROR"
        assert "hard_fail" in (result.reason or "")

    def test_needs_replan(self):
        from unittest.mock import patch as mock_patch

        from src.agents.hooks.verification_hooks import TaskVerificationHook
        from src.verification.base import (
            VerificationReport,
            VerificationResult,
            VerificationScope,
            VerificationVerdict,
        )

        fake_result = VerificationResult(
            verdict=VerificationVerdict.NEEDS_REPLAN,
            report=VerificationReport(
                verifier_name="test",
                scope=VerificationScope.TASK_RESULT,
                verdict=VerificationVerdict.NEEDS_REPLAN,
                summary="needs improvement",
            ),
        )
        hook = TaskVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_TASK_COMPLETE,
            node_name="executor",
            metadata={
                "task": {"task_id": "t1", "description": "test"},
                "assigned_agent": "a",
                "task_result": "r",
                "verified_facts": {},
                "artifacts": [],
            },
        )
        with mock_patch("src.verification.runtime.run_task_verification", return_value=fake_result):
            result = hook.handle(ctx)
        assert result.decision == HookDecision.SHORT_CIRCUIT
        assert result.update_patch["execution_state"] == "EXECUTING_DONE"
        assert "verification_feedback" in result.update_patch
        assert "needs_replan" in (result.reason or "")


class TestWorkflowVerificationHook:
    def test_passed(self):
        from src.agents.hooks.verification_hooks import WorkflowVerificationHook

        hook = WorkflowVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT,
            node_name="planner",
            metadata={
                "final_result": "summary",
                "task_pool": [],
                "verified_facts": {},
                "workflow_kind": None,
                "verification_retry_count": 0,
                "original_input": "test",
                "run_id": "run_123",
                "planner_goal": "test goal",
            },
        )
        result = hook.handle(ctx)
        assert result.decision == HookDecision.CONTINUE
        assert result.update_patch.get("workflow_verification_status") == "passed"
        assert result.update_patch.get("verification_retry_count") == 0

    def test_hard_fail(self):
        from unittest.mock import patch as mock_patch

        from src.agents.hooks.verification_hooks import WorkflowVerificationHook
        from src.verification.base import (
            VerificationReport,
            VerificationResult,
            VerificationScope,
            VerificationVerdict,
        )

        fake_result = VerificationResult(
            verdict=VerificationVerdict.HARD_FAIL,
            report=VerificationReport(
                verifier_name="test",
                scope=VerificationScope.WORKFLOW_RESULT,
                verdict=VerificationVerdict.HARD_FAIL,
                summary="critical",
            ),
        )
        hook = WorkflowVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT,
            node_name="planner",
            metadata={
                "final_result": "s",
                "task_pool": [],
                "verified_facts": {},
                "verification_retry_count": 0,
                "original_input": "i",
                "run_id": "r",
                "planner_goal": "g",
            },
        )
        with mock_patch("src.verification.runtime.run_workflow_verification", return_value=fake_result):
            result = hook.handle(ctx)
        assert result.decision == HookDecision.SHORT_CIRCUIT
        assert result.update_patch["execution_state"] == "ERROR"

    def test_needs_replan_within_budget(self):
        from unittest.mock import patch as mock_patch

        from src.agents.hooks.verification_hooks import WorkflowVerificationHook
        from src.verification.base import (
            VerificationReport,
            VerificationResult,
            VerificationScope,
            VerificationVerdict,
        )

        fake_result = VerificationResult(
            verdict=VerificationVerdict.NEEDS_REPLAN,
            report=VerificationReport(
                verifier_name="test",
                scope=VerificationScope.WORKFLOW_RESULT,
                verdict=VerificationVerdict.NEEDS_REPLAN,
                summary="retry",
            ),
        )
        hook = WorkflowVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT,
            node_name="planner",
            metadata={
                "final_result": "s",
                "task_pool": [],
                "verified_facts": {},
                "verification_retry_count": 0,
                "original_input": "i",
                "run_id": "r",
                "planner_goal": "g",
            },
        )
        with mock_patch("src.verification.runtime.run_workflow_verification", return_value=fake_result):
            result = hook.handle(ctx)
        assert result.decision == HookDecision.SHORT_CIRCUIT
        assert result.update_patch["execution_state"] == "QUEUED"
        assert result.update_patch["verification_retry_count"] == 1

    def test_needs_replan_budget_exhausted(self):
        from unittest.mock import patch as mock_patch

        from src.agents.hooks.verification_hooks import WorkflowVerificationHook
        from src.verification.base import (
            VerificationReport,
            VerificationResult,
            VerificationScope,
            VerificationVerdict,
        )

        fake_result = VerificationResult(
            verdict=VerificationVerdict.NEEDS_REPLAN,
            report=VerificationReport(
                verifier_name="test",
                scope=VerificationScope.WORKFLOW_RESULT,
                verdict=VerificationVerdict.NEEDS_REPLAN,
                summary="retry",
            ),
        )
        hook = WorkflowVerificationHook()
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT,
            node_name="planner",
            metadata={
                "final_result": "s",
                "task_pool": [],
                "verified_facts": {},
                "verification_retry_count": 3,  # at limit
                "original_input": "i",
                "run_id": "r",
                "planner_goal": "g",
            },
        )
        with mock_patch("src.verification.runtime.run_workflow_verification", return_value=fake_result):
            result = hook.handle(ctx)
        assert result.decision == HookDecision.SHORT_CIRCUIT
        assert result.update_patch["execution_state"] == "ERROR"
        assert "budget_exhausted" in (result.reason or "")


# ===========================================================================
# 5. install_default_runtime_hooks
# ===========================================================================

class TestInstallDefaultHooks:
    def test_installs_on_fresh_registry(self):
        from src.agents.hooks.verification_hooks import install_default_runtime_hooks

        reg = RuntimeHookRegistry()
        install_default_runtime_hooks(registry=reg)
        assert reg.has_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)
        assert reg.has_handlers(RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT)

    def test_idempotent(self):
        from src.agents.hooks.verification_hooks import install_default_runtime_hooks

        reg = RuntimeHookRegistry()
        install_default_runtime_hooks(registry=reg)
        install_default_runtime_hooks(registry=reg)
        assert len(reg.get_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)) == 1
        assert len(reg.get_handlers(RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT)) == 1

    def test_reinstalls_after_clear(self):
        """After registry.clear(), ensure_default_hooks re-installs defaults."""
        from src.agents.hooks.runner import ensure_default_hooks
        from src.agents.hooks.registry import runtime_hook_registry

        # Ensure defaults are installed on the global registry
        ensure_default_hooks()
        assert runtime_hook_registry.has_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)

        # Clear and verify gone
        runtime_hook_registry.clear()
        assert not runtime_hook_registry.has_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)

        # ensure_default_hooks should re-install them
        ensure_default_hooks()
        assert runtime_hook_registry.has_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)
        assert runtime_hook_registry.has_handlers(RuntimeHookName.BEFORE_FINAL_RESULT_COMMIT)

        # cleanup
        runtime_hook_registry.clear()

    def test_custom_handler_does_not_block_default_install(self):
        """Custom handlers on same hook point should not prevent default verifier install."""
        from src.agents.hooks.verification_hooks import install_default_runtime_hooks

        reg = RuntimeHookRegistry()

        # Add a custom handler first
        class CustomHook(RuntimeHookHandler):
            name = "my_custom_hook"
            def handle(self, ctx):
                return RuntimeHookResult.ok()

        reg.register(RuntimeHookName.AFTER_TASK_COMPLETE, CustomHook())
        assert len(reg.get_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)) == 1

        # install_default_runtime_hooks should still add the default verifier
        install_default_runtime_hooks(registry=reg)
        assert len(reg.get_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)) == 2
        handler_names = [h.name for h in reg.get_handlers(RuntimeHookName.AFTER_TASK_COMPLETE)]
        assert "my_custom_hook" in handler_names
        assert "task_verification" in handler_names

    def test_empty_registry_for_after_node_hooks(self):
        from src.agents.hooks.verification_hooks import install_default_runtime_hooks

        reg = RuntimeHookRegistry()
        install_default_runtime_hooks(registry=reg)
        # after_planner / after_router / after_executor should have NO handlers
        assert not reg.has_handlers(RuntimeHookName.AFTER_PLANNER)
        assert not reg.has_handlers(RuntimeHookName.AFTER_ROUTER)
        assert not reg.has_handlers(RuntimeHookName.AFTER_EXECUTOR)


# ===========================================================================
# 6. Empty registry zero-behaviour-change
# ===========================================================================

class TestEmptyRegistryZeroBehaviourChange:
    def test_all_hook_points_passthrough(self):
        reg = RuntimeHookRegistry()
        for hook_name in RuntimeHookName:
            original = {"execution_state": "DONE", "test_key": hook_name.value}
            result = run_runtime_hooks(
                hook_name,
                node_name="test",
                state={},
                proposed_update=original,
                registry=reg,
            )
            assert result is original  # same object, not even copied
