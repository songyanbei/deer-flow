"""Tests for the dependency-aware parallel scheduler (Phase 2 Stage 1)."""

from __future__ import annotations

import pytest

from src.agents.scheduler import (
    DEFAULT_CONCURRENCY_WINDOW,
    all_tasks_terminal,
    get_blocked_by_failed_dependency,
    get_currently_executing_count,
    get_runnable_tasks,
    has_pending_work,
    select_execution_batch,
)


def _task(task_id: str, status: str = "PENDING", depends_on=None, priority=None, **kwargs):
    t = {
        "task_id": task_id,
        "description": f"Task {task_id}",
        "status": status,
    }
    if depends_on:
        t["depends_on_task_ids"] = depends_on
    if priority is not None:
        t["priority"] = priority
    t.update(kwargs)
    return t


class TestGetRunnableTasks:
    def test_empty_pool(self):
        assert get_runnable_tasks([]) == []

    def test_all_pending_no_deps(self):
        pool = [_task("a"), _task("b"), _task("c")]
        runnable = get_runnable_tasks(pool)
        assert [t["task_id"] for t in runnable] == ["a", "b", "c"]

    def test_pending_with_satisfied_deps(self):
        pool = [
            _task("a", status="DONE"),
            _task("b", depends_on=["a"]),
        ]
        runnable = get_runnable_tasks(pool)
        assert [t["task_id"] for t in runnable] == ["b"]

    def test_pending_with_unsatisfied_deps(self):
        pool = [
            _task("a", status="RUNNING"),
            _task("b", depends_on=["a"]),
        ]
        runnable = get_runnable_tasks(pool)
        assert runnable == []

    def test_mixed_dep_satisfaction(self):
        pool = [
            _task("a", status="DONE"),
            _task("b", status="RUNNING"),
            _task("c", depends_on=["a"]),  # runnable
            _task("d", depends_on=["b"]),  # not runnable
            _task("e"),  # runnable (no deps)
        ]
        runnable = get_runnable_tasks(pool)
        ids = [t["task_id"] for t in runnable]
        assert "c" in ids
        assert "e" in ids
        assert "d" not in ids

    def test_priority_ordering(self):
        pool = [
            _task("a", priority=2),
            _task("b", priority=0),
            _task("c", priority=1),
        ]
        runnable = get_runnable_tasks(pool)
        assert [t["task_id"] for t in runnable] == ["b", "c", "a"]

    def test_priority_none_treated_as_zero(self):
        pool = [
            _task("a", priority=1),
            _task("b"),  # no priority → treated as 0
        ]
        runnable = get_runnable_tasks(pool)
        assert [t["task_id"] for t in runnable] == ["b", "a"]

    def test_unknown_dependency_blocks(self):
        pool = [
            _task("a", depends_on=["nonexistent"]),
        ]
        runnable = get_runnable_tasks(pool)
        assert runnable == []

    def test_chain_dependency(self):
        """a -> b -> c: only a is runnable."""
        pool = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
        ]
        runnable = get_runnable_tasks(pool)
        assert [t["task_id"] for t in runnable] == ["a"]

    def test_diamond_dependency(self):
        """a and b independent; c depends on both."""
        pool = [
            _task("a"),
            _task("b"),
            _task("c", depends_on=["a", "b"]),
        ]
        runnable = get_runnable_tasks(pool)
        ids = [t["task_id"] for t in runnable]
        assert "a" in ids
        assert "b" in ids
        assert "c" not in ids

    def test_diamond_dependency_both_done(self):
        pool = [
            _task("a", status="DONE"),
            _task("b", status="DONE"),
            _task("c", depends_on=["a", "b"]),
        ]
        runnable = get_runnable_tasks(pool)
        assert [t["task_id"] for t in runnable] == ["c"]


class TestSelectExecutionBatch:
    def test_respects_concurrency_window(self):
        pool = [_task(str(i)) for i in range(10)]
        batch = select_execution_batch(pool, max_concurrency=3)
        assert len(batch) == 3

    def test_accounts_for_running_tasks(self):
        pool = [
            _task("running1", status="RUNNING"),
            _task("running2", status="RUNNING"),
            _task("a"),
            _task("b"),
        ]
        batch = select_execution_batch(pool, max_concurrency=3)
        assert len(batch) == 1
        assert batch[0]["task_id"] == "a"

    def test_no_slots_available(self):
        pool = [
            _task("r1", status="RUNNING"),
            _task("r2", status="RUNNING"),
            _task("r3", status="RUNNING"),
            _task("a"),
        ]
        batch = select_execution_batch(pool, max_concurrency=3)
        assert batch == []

    def test_dependency_aware_batch(self):
        pool = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c"),
        ]
        batch = select_execution_batch(pool, max_concurrency=3)
        ids = [t["task_id"] for t in batch]
        assert "a" in ids
        assert "c" in ids
        assert "b" not in ids

    def test_default_window_value(self):
        assert DEFAULT_CONCURRENCY_WINDOW == 3

    def test_priority_in_batch(self):
        pool = [
            _task("low", priority=10),
            _task("high", priority=0),
            _task("mid", priority=5),
            _task("extra1"),
            _task("extra2"),
        ]
        batch = select_execution_batch(pool, max_concurrency=2)
        assert len(batch) == 2
        assert batch[0]["task_id"] == "high"
        assert batch[1]["task_id"] == "extra1"  # priority None → 0, same as "high"


class TestHelperFunctions:
    def test_get_currently_executing_count(self):
        pool = [
            _task("a", status="RUNNING"),
            _task("b", status="PENDING"),
            _task("c", status="RUNNING"),
        ]
        assert get_currently_executing_count(pool) == 2

    def test_has_pending_work(self):
        assert has_pending_work([_task("a")]) is True
        assert has_pending_work([_task("a", status="RUNNING")]) is True
        assert has_pending_work([_task("a", status="WAITING_DEPENDENCY")]) is True
        assert has_pending_work([_task("a", status="DONE")]) is False
        assert has_pending_work([_task("a", status="FAILED")]) is False
        assert has_pending_work([]) is False

    def test_all_tasks_terminal(self):
        assert all_tasks_terminal([]) is True
        assert all_tasks_terminal([_task("a", status="DONE")]) is True
        assert all_tasks_terminal([_task("a", status="FAILED")]) is True
        assert all_tasks_terminal([_task("a", status="DONE"), _task("b", status="FAILED")]) is True
        assert all_tasks_terminal([_task("a", status="DONE"), _task("b", status="PENDING")]) is False

    def test_get_blocked_by_failed_dependency(self):
        pool = [
            _task("a", status="FAILED"),
            _task("b", depends_on=["a"]),
            _task("c"),  # no deps, should not be blocked
        ]
        blocked = get_blocked_by_failed_dependency(pool)
        assert len(blocked) == 1
        assert blocked[0]["task_id"] == "b"

    def test_get_blocked_no_failed(self):
        pool = [
            _task("a", status="DONE"),
            _task("b", depends_on=["a"]),
        ]
        blocked = get_blocked_by_failed_dependency(pool)
        assert blocked == []
