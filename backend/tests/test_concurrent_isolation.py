"""Tests for Phase 2 Stage 1 concurrency isolation guarantees.

Covers:
1. Router: clarification answer bound to exactly one task per resume
2. Executor: clarification answer read from task resolved_inputs, not global state
3. Executor: intervention answer is task-scoped
4. Executor: concurrency window hard cap
5. Router: intervention resolution matched by request_id
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from src.agents.thread_state import TaskStatus, ThreadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str,
    status: str = "RUNNING",
    agent: str = "test-agent",
    **overrides: Any,
) -> TaskStatus:
    base: TaskStatus = {
        "task_id": task_id,
        "description": f"Task {task_id}",
        "status": status,
        "assigned_agent": agent,
        "run_id": "run-1",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _make_state(task_pool: list[TaskStatus], **overrides: Any) -> ThreadState:
    state: ThreadState = {
        "messages": [HumanMessage(content="hello")],
        "task_pool": task_pool,
        "execution_state": "ROUTING_DONE",
        "route_count": 1,
    }
    state.update(overrides)
    return state


def _user_clarification_task(task_id: str, agent: str = "test-agent", payload: dict | None = None) -> TaskStatus:
    """Build a minimal task with user_clarification intervention (resolved)."""
    action = {
        "key": "submit_response",
        "label": "Submit",
        "kind": "input",
        "resolution_behavior": "resume_current_task",
    }
    return _make_task(
        task_id,
        status="RUNNING",
        agent=agent,
        continuation_mode="continue_after_intervention",
        intervention_status="resolved",
        intervention_request={
            "request_id": f"req-{task_id}",
            "fingerprint": f"fp-{task_id}",
            "source_signal": "request_help",
            "intervention_type": "clarification",
            "category": "user_clarification",
            "source_agent": agent,
            "source_task_id": task_id,
            "action_schema": {"actions": [action]},
            "created_at": "2026-01-01T00:00:00Z",
            "title": "Need info",
            "reason": "Need info",
        },
        intervention_resolution={
            "request_id": f"req-{task_id}",
            "fingerprint": f"fp-{task_id}",
            "action_key": "submit_response",
            "payload": payload or {},
            "resolution_behavior": "resume_current_task",
        },
    )


# ===================================================================
# 1. Router: clarification answer binding
# ===================================================================

class TestRouterClarificationBinding:
    """Router must bind clarification answer to exactly one task per resume."""

    def test_answer_bound_to_first_clarification_task_only(self):
        """When two tasks are continue_after_clarification, only the first
        gets the answer in resolved_inputs; the second gets nothing."""
        t1 = _make_task("t1", continuation_mode="continue_after_clarification")
        t2 = _make_task("t2", continuation_mode="continue_after_clarification")
        state = _make_state([t1, t2])

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.extract_latest_clarification_answer", return_value="Answer for t1"),
            ):
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                return await router_node(state, config)

        result = asyncio.run(_run())

        updated_pool = result.get("task_pool", [])
        assert len(updated_pool) == 1
        bound_task = updated_pool[0]
        assert bound_task["task_id"] == "t1"
        assert bound_task["resolved_inputs"]["clarification_answer"] == "Answer for t1"

    def test_no_binding_when_no_clarification_answer(self):
        """When there is no clarification answer in messages, no binding occurs."""
        t1 = _make_task("t1", continuation_mode="continue_after_clarification")
        state = _make_state([t1])

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.extract_latest_clarification_answer", return_value=""),
            ):
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                return await router_node(state, config)

        result = asyncio.run(_run())
        assert "task_pool" not in result

    def test_no_binding_for_non_clarification_running_tasks(self):
        """Regular RUNNING tasks (no continuation_mode) should never get answer bound."""
        t1 = _make_task("t1")  # plain RUNNING, no continuation_mode
        state = _make_state([t1])

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.extract_latest_clarification_answer", return_value="stale answer"),
            ):
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                return await router_node(state, config)

        result = asyncio.run(_run())
        assert "task_pool" not in result


# ===================================================================
# 2. Executor: clarification answer reads from task, not global state
# ===================================================================

class TestExecutorClarificationIsolation:
    """Executor must read clarification answer from task.resolved_inputs only."""

    def test_clarification_answer_from_resolved_inputs(self):
        """continue_after_clarification task reads answer from resolved_inputs."""
        from src.agents.executor.executor import _build_context

        task = _make_task(
            "t1",
            continuation_mode="continue_after_clarification",
            resolved_inputs={"clarification_answer": "Alice"},
        )
        continuation_mode = task.get("continuation_mode")
        assert continuation_mode == "continue_after_clarification"
        clarification_answer = (task.get("resolved_inputs") or {}).get("clarification_answer", "")
        context = _build_context(task, {}, clarification_answer)
        assert "Alice" in context

    def test_no_answer_when_not_bound(self):
        """continue_after_clarification task with no resolved_inputs gets empty answer."""
        task = _make_task("t2", continuation_mode="continue_after_clarification")
        clarification_answer = (task.get("resolved_inputs") or {}).get("clarification_answer", "")
        assert clarification_answer == ""

    def test_no_answer_for_regular_running_task(self):
        """Regular RUNNING task gets no clarification answer regardless of global state."""
        task = _make_task("t3")
        continuation_mode = task.get("continuation_mode")
        assert continuation_mode is None

    def test_concurrent_tasks_get_independent_answers(self):
        """Two clarification tasks with different answers don't cross-contaminate."""
        t1 = _make_task("t1", continuation_mode="continue_after_clarification",
                        resolved_inputs={"clarification_answer": "Answer A"})
        t2 = _make_task("t2", continuation_mode="continue_after_clarification",
                        resolved_inputs={"clarification_answer": "Answer B"})

        answers = {}
        for task in [t1, t2]:
            cm = task.get("continuation_mode")
            if cm == "continue_after_clarification":
                answers[task["task_id"]] = (task.get("resolved_inputs") or {}).get("clarification_answer", "")
            else:
                answers[task["task_id"]] = ""

        assert answers["t1"] == "Answer A"
        assert answers["t2"] == "Answer B"


# ===================================================================
# 3. Executor: intervention answer is task-scoped
# ===================================================================

class TestExecutorInterventionIsolation:
    """Intervention answers must come from the task itself, not global state."""

    def test_intervention_answer_from_task(self):
        """continue_after_intervention reads from task's intervention data."""
        from src.agents.workflow_resume import normalize_intervention_clarification_answer

        task = _user_clarification_task("t1", payload={"text": "Bob"})
        answer = normalize_intervention_clarification_answer(task)
        assert answer == "Bob"

    def test_two_intervention_tasks_get_own_answers(self):
        """Two concurrent intervention tasks each read their own resolution."""
        from src.agents.workflow_resume import normalize_intervention_clarification_answer

        t1 = _user_clarification_task("t1", payload={"text": "Alice"})
        t2 = _user_clarification_task("t2", payload={"text": "Bob"})

        assert normalize_intervention_clarification_answer(t1) == "Alice"
        assert normalize_intervention_clarification_answer(t2) == "Bob"


# ===================================================================
# 4. Executor: concurrency window hard cap
# ===================================================================

class TestExecutorConcurrencyWindowCap:
    """Executor must enforce hard concurrency cap even if state has excess RUNNING tasks."""

    def test_excess_running_tasks_truncated(self):
        """4 RUNNING tasks with window=3: only first 3 execute, 4th deferred."""
        from src.agents.scheduler import DEFAULT_CONCURRENCY_WINDOW

        executed_ids: list[str] = []

        async def fake_execute_single(task, state, config, writer):
            executed_ids.append(task["task_id"])
            return {
                "task_pool": [{**task, "status": "DONE"}],
                "execution_state": "EXECUTING_DONE",
            }

        tasks = [_make_task(f"t{i}") for i in range(4)]
        state = _make_state(tasks)

        async def _run():
            with (
                patch("src.agents.executor.executor._get_event_writer", return_value=lambda e: None),
                patch("src.agents.executor.executor._execute_single_task", side_effect=fake_execute_single),
                patch("src.agents.executor.executor._merge_task_results") as mock_merge,
            ):
                mock_merge.return_value = {"execution_state": "EXECUTING_DONE", "task_pool": []}
                from src.agents.executor.executor import executor_node
                config = {"configurable": {"thread_id": "test"}}
                await executor_node(state, config)

        asyncio.run(_run())
        assert len(executed_ids) == DEFAULT_CONCURRENCY_WINDOW
        assert executed_ids == ["t0", "t1", "t2"]

    def test_within_window_no_truncation(self):
        """2 RUNNING tasks with window=3: all execute, no truncation."""
        executed_ids: list[str] = []

        async def fake_execute_single(task, state, config, writer):
            executed_ids.append(task["task_id"])
            return {
                "task_pool": [{**task, "status": "DONE"}],
                "execution_state": "EXECUTING_DONE",
            }

        tasks = [_make_task(f"t{i}") for i in range(2)]
        state = _make_state(tasks)

        async def _run():
            with (
                patch("src.agents.executor.executor._get_event_writer", return_value=lambda e: None),
                patch("src.agents.executor.executor._execute_single_task", side_effect=fake_execute_single),
                patch("src.agents.executor.executor._merge_task_results") as mock_merge,
            ):
                mock_merge.return_value = {"execution_state": "EXECUTING_DONE", "task_pool": []}
                from src.agents.executor.executor import executor_node
                config = {"configurable": {"thread_id": "test"}}
                await executor_node(state, config)

        asyncio.run(_run())
        assert len(executed_ids) == 2


# ===================================================================
# 5. Router: intervention resolution matched by request_id
# ===================================================================

class TestRouterInterventionMatching:
    """Router must match intervention resolution to correct task by request_id."""

    def test_matches_by_request_id(self):
        """When two tasks wait for intervention, resolution targets the correct one."""
        t1 = _make_task("t1", status="WAITING_INTERVENTION", agent="agent-a",
                        intervention_request={"request_id": "req-1", "fingerprint": "fp-1"})
        t2 = _make_task("t2", status="WAITING_INTERVENTION", agent="agent-b",
                        intervention_request={"request_id": "req-2", "fingerprint": "fp-2"})
        state = _make_state([t1, t2], execution_state="INTERRUPTED")
        state["messages"] = [HumanMessage(content="[intervention_resolved] request_id=req-2 action_key=approve")]

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.is_intervention_resolution_message", return_value=True),
                patch("src.agents.router.semantic_router.apply_intervention_resolution") as mock_apply,
                patch("src.agents.router.semantic_router.build_cached_intervention_entry", return_value=(None, None)),
            ):
                mock_apply.return_value = (
                    {**t2, "status": "RUNNING",
                     "intervention_resolution": {"request_id": "req-2", "action_key": "approve", "resolution_behavior": "resume_current_task"},
                     "updated_at": "2026-01-01T00:00:01Z"},
                    None,
                )
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                result = await router_node(state, config)
                return result, mock_apply

        result, mock_apply = asyncio.run(_run())
        mock_apply.assert_called_once()
        applied_task = mock_apply.call_args[0][0]
        assert applied_task["task_id"] == "t2"

    def test_ambiguous_resolution_returns_interrupted(self):
        """When request_id doesn't match any waiting task, return INTERRUPTED."""
        t1 = _make_task("t1", status="WAITING_INTERVENTION",
                        intervention_request={"request_id": "req-1", "fingerprint": "fp-1"})
        t2 = _make_task("t2", status="WAITING_INTERVENTION",
                        intervention_request={"request_id": "req-2", "fingerprint": "fp-2"})
        state = _make_state([t1, t2], execution_state="INTERRUPTED")
        state["messages"] = [HumanMessage(content="[intervention_resolved] request_id=req-unknown action_key=approve")]

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.is_intervention_resolution_message", return_value=True),
            ):
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                return await router_node(state, config)

        result = asyncio.run(_run())
        assert result["execution_state"] == "INTERRUPTED"

    def test_single_waiting_task_fallback(self):
        """Single waiting task is used even without request_id match (backward compat)."""
        t1 = _make_task("t1", status="WAITING_INTERVENTION",
                        intervention_request={"request_id": "req-1", "fingerprint": "fp-1"})
        state = _make_state([t1], execution_state="INTERRUPTED")
        state["messages"] = [HumanMessage(content="[intervention_resolved] action_key=approve")]

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.is_intervention_resolution_message", return_value=True),
                patch("src.agents.router.semantic_router.apply_intervention_resolution") as mock_apply,
                patch("src.agents.router.semantic_router.build_cached_intervention_entry", return_value=(None, None)),
            ):
                mock_apply.return_value = (
                    {**t1, "status": "RUNNING",
                     "intervention_resolution": {"request_id": "req-1", "action_key": "approve", "resolution_behavior": "resume_current_task"},
                     "updated_at": "2026-01-01T00:00:01Z"},
                    None,
                )
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                result = await router_node(state, config)
                return result, mock_apply

        result, mock_apply = asyncio.run(_run())
        mock_apply.assert_called_once()
        applied_task = mock_apply.call_args[0][0]
        assert applied_task["task_id"] == "t1"

    def test_gateway_written_resolution_takes_priority(self):
        """When Gateway already wrote resolution onto a task, use that task."""
        t1 = _make_task("t1", status="WAITING_INTERVENTION",
                        intervention_request={"request_id": "req-1", "fingerprint": "fp-1"})
        t2 = _make_task("t2", status="WAITING_INTERVENTION",
                        intervention_request={"request_id": "req-2", "fingerprint": "fp-2"},
                        intervention_resolution={"request_id": "req-2", "action_key": "approve",
                                                  "resolution_behavior": "resume_current_task"})
        state = _make_state([t1, t2], execution_state="INTERRUPTED")
        state["messages"] = [HumanMessage(content="[intervention_resolved] request_id=req-2 action_key=approve")]

        async def _run():
            with (
                patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda e: None),
                patch("src.agents.router.semantic_router.is_intervention_resolution_message", return_value=True),
                patch("src.agents.router.semantic_router.apply_intervention_resolution") as mock_apply,
                patch("src.agents.router.semantic_router.build_cached_intervention_entry", return_value=(None, None)),
            ):
                mock_apply.return_value = (
                    {**t2, "status": "RUNNING", "updated_at": "2026-01-01T00:00:01Z"},
                    None,
                )
                from src.agents.router.semantic_router import router_node
                config = {"configurable": {"thread_id": "test"}}
                result = await router_node(state, config)
                return result, mock_apply

        result, mock_apply = asyncio.run(_run())
        mock_apply.assert_called_once()
        applied_task = mock_apply.call_args[0][0]
        assert applied_task["task_id"] == "t2"
