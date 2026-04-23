"""Regression: executor must forward thread/tenant/user identity into the
``before_interrupt_emit`` lifecycle hook so the governance audit hook can
persist a ledger entry bound to the originating thread/tenant/user.

Without this wiring, ``governance_ledger`` writes an empty ``thread_id``
and ``null`` ``user_id`` for every workflow interrupt, and the Gateway
``POST /api/runtime/threads/{id}/governance:resume`` ownership validation
(Phase 2.2 P1-A) then 403s every real resume.

These tests pin the executor → lifecycle → audit hook identity contract.
"""

from __future__ import annotations

from unittest.mock import patch

from src.agents.executor import executor as executor_module


class TestApplyBeforeInterruptEmitIdentity:
    def test_identity_flows_into_lifecycle_call(self):
        """``thread_id`` is a top-level hook param; ``tenant_id`` / ``user_id``
        ride along in ``extra_metadata`` because they are not part of the
        canonical lifecycle signature.
        """
        with patch(
            "src.agents.hooks.lifecycle.apply_before_interrupt_emit"
        ) as mock_apply:
            mock_apply.return_value = {"execution_state": "INTERRUPTED"}
            executor_module._apply_before_interrupt_emit_safe(
                interrupt_type="intervention",
                task={"task_id": "task-9"},
                agent_name="research-agent",
                source_path="executor.request_intervention",
                proposed_update={"execution_state": "INTERRUPTED"},
                state={"foo": "bar"},
                run_id="run-9",
                thread_id="thread-abc",
                tenant_id="tenant-x",
                user_id="user-7",
            )
            assert mock_apply.call_count == 1
            kwargs = mock_apply.call_args.kwargs
            assert kwargs["thread_id"] == "thread-abc"
            assert kwargs["run_id"] == "run-9"
            assert kwargs["extra_metadata"] == {
                "tenant_id": "tenant-x",
                "user_id": "user-7",
            }

    def test_missing_identity_sends_none_extra_metadata(self):
        """Callers that genuinely don't have identity (legacy tests, system
        paths) must not accidentally set ``extra_metadata={}`` — the
        lifecycle helper treats ``None`` and ``{}`` the same, but we pass
        ``None`` for clarity.
        """
        with patch(
            "src.agents.hooks.lifecycle.apply_before_interrupt_emit"
        ) as mock_apply:
            mock_apply.return_value = {"execution_state": "INTERRUPTED"}
            executor_module._apply_before_interrupt_emit_safe(
                interrupt_type="clarification",
                task={"task_id": "task-1"},
                agent_name="system",
                source_path="executor.request_clarification",
                proposed_update={"execution_state": "INTERRUPTED"},
                state={},
                run_id="run-1",
            )
            kwargs = mock_apply.call_args.kwargs
            assert kwargs["thread_id"] is None
            assert kwargs["extra_metadata"] is None

    def test_audit_hook_reads_identity_via_metadata_and_ctx(self):
        """End-to-end through the real lifecycle + audit hook: a ledger write
        carries the forwarded thread_id / tenant_id / user_id.
        """
        from src.agents.governance.audit_hooks import (
            GovernanceInterruptEmitAuditHook,
        )
        from src.agents.governance.engine import GovernanceEngine
        from src.agents.hooks.base import RuntimeHookName
        from src.agents.hooks.registry import RuntimeHookRegistry

        captured: dict[str, object] = {}

        class _FakeEngine(GovernanceEngine):  # pylint: disable=too-few-public-methods
            def record_interrupt_emit(self, **kwargs):  # type: ignore[override]
                captured.update(kwargs)
                return None

        registry = RuntimeHookRegistry()
        registry.register(
            RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            GovernanceInterruptEmitAuditHook(engine=_FakeEngine()),
        )

        with patch(
            "src.agents.hooks.runner.runtime_hook_registry", registry
        ):
            executor_module._apply_before_interrupt_emit_safe(
                interrupt_type="intervention",
                task={"task_id": "task-9"},
                agent_name="research-agent",
                source_path="executor.request_intervention",
                proposed_update={"task_pool": [{"task_id": "task-9"}]},
                state={},
                run_id="run-9",
                thread_id="thread-abc",
                tenant_id="tenant-x",
                user_id="user-7",
            )

        assert captured.get("thread_id") == "thread-abc"
        assert captured.get("run_id") == "run-9"
        assert captured.get("task_id") == "task-9"
        assert captured.get("tenant_id") == "tenant-x"
        assert captured.get("user_id") == "user-7"
