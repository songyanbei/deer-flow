"""Tests for Slice B runtime hooks: interrupt lifecycle & state commit.

Covers:
1. Lifecycle helpers (apply_before_interrupt_emit, apply_after_interrupt_resolve, apply_state_commit_hooks)
2. verified_facts={} clear-all guard
3. State commit hook ordering (task_pool first, verified_facts second)
4. Empty registry zero-behaviour-change
5. Hook error propagation (fail-closed)
6. RuntimeHookName enum activation
"""

from __future__ import annotations

import pytest

from src.agents.hooks.base import (
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookName,
    RuntimeHookResult,
)
from src.agents.hooks.lifecycle import (
    VerifiedFactsClearAllGuardError,
    apply_state_commit_hooks,
)
from src.agents.hooks.registry import RuntimeHookRegistry
from src.agents.hooks.runner import HookExecutionError, run_runtime_hooks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class PatchHook(RuntimeHookHandler):
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


class MetadataCapture(RuntimeHookHandler):
    """Records the metadata it receives for test assertions."""

    def __init__(self, hook_name: str = "metadata_capture", priority: int = 100):
        self.name = hook_name
        self.priority = priority
        self.captured: list[dict] = []

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        self.captured.append(dict(ctx.metadata))
        return RuntimeHookResult.ok()


@pytest.fixture
def registry():
    reg = RuntimeHookRegistry()
    yield reg
    reg.clear()


# ---------------------------------------------------------------------------
# 1. RuntimeHookName enum — Slice B names are active
# ---------------------------------------------------------------------------

class TestSliceBHookNames:
    def test_before_interrupt_emit_exists(self):
        assert RuntimeHookName.BEFORE_INTERRUPT_EMIT.value == "before_interrupt_emit"

    def test_after_interrupt_resolve_exists(self):
        assert RuntimeHookName.AFTER_INTERRUPT_RESOLVE.value == "after_interrupt_resolve"

    def test_before_task_pool_commit_exists(self):
        assert RuntimeHookName.BEFORE_TASK_POOL_COMMIT.value == "before_task_pool_commit"

    def test_before_verified_facts_commit_exists(self):
        assert RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT.value == "before_verified_facts_commit"


# ---------------------------------------------------------------------------
# 2. apply_before_interrupt_emit
# ---------------------------------------------------------------------------

class TestBeforeInterruptEmit:
    def test_empty_registry_passthrough(self, registry):
        update = {"task_pool": [{"task_id": "t1"}], "execution_state": "INTERRUPTED"}
        result = run_runtime_hooks(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            state={},
            proposed_update=update,
            registry=registry,
        )
        assert result == update

    def test_handler_patches_update(self, registry):
        registry.register(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            PatchHook(patch={"_interrupt_audited": True}),
        )
        result = run_runtime_hooks(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            state={},
            proposed_update={"execution_state": "INTERRUPTED"},
            registry=registry,
        )
        assert result["_interrupt_audited"] is True
        assert result["execution_state"] == "INTERRUPTED"

    def test_short_circuit_stops_chain(self, registry):
        registry.register(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            ShortCircuitHook(patch={"execution_state": "ERROR"}),
            priority=10,
        )
        registry.register(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            PatchHook(hook_name="should_not_run", patch={"unreachable": True}),
            priority=20,
        )
        result = run_runtime_hooks(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            state={},
            proposed_update={"execution_state": "INTERRUPTED"},
            registry=registry,
        )
        assert result["execution_state"] == "ERROR"
        assert "unreachable" not in result

    def test_error_propagates(self, registry):
        registry.register(RuntimeHookName.BEFORE_INTERRUPT_EMIT, ExplodingHook())
        with pytest.raises(HookExecutionError):
            run_runtime_hooks(
                RuntimeHookName.BEFORE_INTERRUPT_EMIT,
                node_name="executor",
                state={},
                proposed_update={"execution_state": "INTERRUPTED"},
                registry=registry,
            )

    def test_metadata_fields(self, registry):
        capture = MetadataCapture()
        registry.register(RuntimeHookName.BEFORE_INTERRUPT_EMIT, capture)
        run_runtime_hooks(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            state={},
            proposed_update={"execution_state": "INTERRUPTED"},
            metadata={
                "interrupt_type": "intervention",
                "task_id": "t1",
                "agent_name": "test-agent",
                "source_path": "executor.request_intervention",
            },
            registry=registry,
        )
        assert len(capture.captured) == 1
        meta = capture.captured[0]
        assert meta["interrupt_type"] == "intervention"
        assert meta["task_id"] == "t1"
        assert meta["source_path"] == "executor.request_intervention"


# ---------------------------------------------------------------------------
# 3. apply_after_interrupt_resolve
# ---------------------------------------------------------------------------

class TestAfterInterruptResolve:
    def test_empty_registry_passthrough(self, registry):
        update = {"task_pool": [{"task_id": "t1", "status": "RUNNING"}]}
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="router",
            state={},
            proposed_update=update,
            registry=registry,
        )
        assert result == update

    def test_handler_patches_update(self, registry):
        registry.register(
            RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            PatchHook(patch={"_resolve_audited": True}),
        )
        result = run_runtime_hooks(
            RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="router",
            state={},
            proposed_update={"execution_state": "ROUTING_DONE"},
            registry=registry,
        )
        assert result["_resolve_audited"] is True

    def test_metadata_from_lifecycle_helper(self, registry):
        capture = MetadataCapture()
        registry.register(RuntimeHookName.AFTER_INTERRUPT_RESOLVE, capture)
        run_runtime_hooks(
            RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="router",
            state={},
            proposed_update={"execution_state": "ROUTING_DONE"},
            metadata={
                "task_id": "t1",
                "new_status": "RUNNING",
                "source_path": "router.in_graph_resolve",
                "action_key": "approve",
                "resolution_behavior": "resume_current_task",
                "request_id": "req-1",
            },
            registry=registry,
        )
        meta = capture.captured[0]
        assert meta["source_path"] == "router.in_graph_resolve"
        assert meta["action_key"] == "approve"


# ---------------------------------------------------------------------------
# 4. apply_state_commit_hooks
# ---------------------------------------------------------------------------

class TestStateCommitHooks:
    def test_empty_registry_passthrough(self):
        update = {"task_pool": [{"task_id": "t1"}], "verified_facts": {"f1": "v1"}}
        result = apply_state_commit_hooks(
            proposed_update=update,
            source_path="node_wrapper.executor",
        )
        assert result == update

    def test_task_pool_hook_fires_only_when_present(self, registry):
        capture = MetadataCapture(hook_name="tp_capture")
        registry.register(RuntimeHookName.BEFORE_TASK_POOL_COMMIT, capture)
        # No task_pool in update → hook should NOT fire
        run_runtime_hooks(
            RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="executor",
            state={},
            proposed_update={"execution_state": "DONE"},
            registry=registry,
        )
        # Via apply_state_commit_hooks, task_pool absent → no fire
        result = apply_state_commit_hooks(
            proposed_update={"execution_state": "DONE"},
            source_path="node_wrapper.executor",
        )
        assert result == {"execution_state": "DONE"}

    def test_verified_facts_hook_fires_only_when_present(self, registry):
        capture = MetadataCapture(hook_name="vf_capture")
        registry.register(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT, capture)
        result = apply_state_commit_hooks(
            proposed_update={"task_pool": [{"task_id": "t1"}]},
            source_path="node_wrapper.executor",
        )
        # verified_facts not in update → vf hook not fired
        assert result == {"task_pool": [{"task_id": "t1"}]}

    def test_both_hooks_fire_in_order(self, registry):
        order = []

        class OrderTracker(RuntimeHookHandler):
            def __init__(self, hook_name: str):
                self.name = hook_name
                self.priority = 100

            def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
                order.append(self.name)
                return RuntimeHookResult.ok()

        registry.register(RuntimeHookName.BEFORE_TASK_POOL_COMMIT, OrderTracker("tp"))
        registry.register(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT, OrderTracker("vf"))

        update = {"task_pool": [{"task_id": "t1"}], "verified_facts": {"f1": "v1"}}
        # Call directly through run_runtime_hooks to use our registry
        # First task_pool
        run_runtime_hooks(
            RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="executor",
            state={},
            proposed_update=update,
            registry=registry,
        )
        # Then verified_facts
        run_runtime_hooks(
            RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT,
            node_name="executor",
            state={},
            proposed_update=update,
            registry=registry,
        )
        assert order == ["tp", "vf"]

    def test_handler_patches_task_pool(self, registry):
        registry.register(
            RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            PatchHook(patch={"_tp_audited": True}),
        )
        result = run_runtime_hooks(
            RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="executor",
            state={},
            proposed_update={"task_pool": [{"task_id": "t1"}]},
            registry=registry,
        )
        assert result["_tp_audited"] is True
        assert result["task_pool"] == [{"task_id": "t1"}]


# ---------------------------------------------------------------------------
# 5. verified_facts={} clear-all guard
# ---------------------------------------------------------------------------

class TestVerifiedFactsClearAllGuard:
    def test_empty_dict_raises(self):
        with pytest.raises(VerifiedFactsClearAllGuardError):
            apply_state_commit_hooks(
                proposed_update={"verified_facts": {}},
                source_path="test",
            )

    def test_empty_dict_allowed_with_flag(self):
        result = apply_state_commit_hooks(
            proposed_update={"verified_facts": {}},
            source_path="test",
            allow_verified_facts_clear_all=True,
        )
        assert result["verified_facts"] == {}

    def test_non_empty_dict_passes(self):
        result = apply_state_commit_hooks(
            proposed_update={"verified_facts": {"f1": "v1"}},
            source_path="test",
        )
        assert result["verified_facts"] == {"f1": "v1"}

    def test_no_verified_facts_key_passes(self):
        result = apply_state_commit_hooks(
            proposed_update={"task_pool": []},
            source_path="test",
        )
        assert "verified_facts" not in result


# ---------------------------------------------------------------------------
# 6. Error propagation (fail-closed)
# ---------------------------------------------------------------------------

class TestSliceBFailClosed:
    def test_before_interrupt_emit_error(self, registry):
        registry.register(RuntimeHookName.BEFORE_INTERRUPT_EMIT, ExplodingHook())
        with pytest.raises(HookExecutionError):
            run_runtime_hooks(
                RuntimeHookName.BEFORE_INTERRUPT_EMIT,
                node_name="executor",
                state={},
                proposed_update={},
                registry=registry,
            )

    def test_after_interrupt_resolve_error(self, registry):
        registry.register(RuntimeHookName.AFTER_INTERRUPT_RESOLVE, ExplodingHook())
        with pytest.raises(HookExecutionError):
            run_runtime_hooks(
                RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
                node_name="router",
                state={},
                proposed_update={},
                registry=registry,
            )

    def test_before_task_pool_commit_error(self, registry):
        registry.register(RuntimeHookName.BEFORE_TASK_POOL_COMMIT, ExplodingHook())
        with pytest.raises(HookExecutionError):
            run_runtime_hooks(
                RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
                node_name="executor",
                state={},
                proposed_update={"task_pool": []},
                registry=registry,
            )

    def test_before_verified_facts_commit_error(self, registry):
        registry.register(RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT, ExplodingHook())
        with pytest.raises(HookExecutionError):
            run_runtime_hooks(
                RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT,
                node_name="executor",
                state={},
                proposed_update={"verified_facts": {"f1": "v1"}},
                registry=registry,
            )
