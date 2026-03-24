from __future__ import annotations

from src.agents.thread_state import merge_task_pool


def test_merge_task_pool_preserves_continuation_fields():
    existing = [
        {
            "task_id": "task-1",
            "description": "book meeting room",
            "status": "WAITING_INTERVENTION",
            "continuation_mode": "resume_tool_call",
            "pending_interrupt": {"interrupt_type": "intervention", "request_id": "req-1"},
            "pending_tool_call": {"tool_name": "meeting_createMeeting", "tool_args": {"roomId": "A"}},
            "agent_history_cutoff": 4,
        }
    ]
    updates = [
        {
            "task_id": "task-1",
            "status": "RUNNING",
            "status_detail": "@intervention_resolved",
        }
    ]

    merged = merge_task_pool(existing, updates)

    assert merged[0]["status"] == "RUNNING"
    assert merged[0]["continuation_mode"] == "resume_tool_call"
    assert merged[0]["pending_interrupt"] == {"interrupt_type": "intervention", "request_id": "req-1"}
    assert merged[0]["pending_tool_call"] == {"tool_name": "meeting_createMeeting", "tool_args": {"roomId": "A"}}
    assert merged[0]["agent_history_cutoff"] == 4


def test_merge_task_pool_supersedes_failed_task_on_redecompose():
    """When planner re-decomposes after a task failure, the new PENDING task
    for the same agent+run should evict the old FAILED task so the pool
    converges to a clean state."""
    existing = [
        {
            "task_id": "task-old-hr",
            "description": "查考勤",
            "status": "FAILED",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
            "error": "clarification rejected",
        },
        {
            "task_id": "task-contacts",
            "description": "查联系人",
            "status": "DONE",
            "assigned_agent": "contacts-agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new-hr",
            "description": "查考勤",
            "status": "PENDING",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    # Old FAILED hr task should be evicted
    assert "task-old-hr" not in task_ids
    # New PENDING hr task should be present
    assert "task-new-hr" in task_ids
    # Unrelated DONE task should survive
    assert "task-contacts" in task_ids
    assert len(merged) == 2


def test_merge_task_pool_no_supersession_across_runs():
    """FAILED tasks from a different run_id should NOT be evicted."""
    existing = [
        {
            "task_id": "task-old",
            "description": "查考勤",
            "status": "FAILED",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new",
            "description": "查考勤",
            "status": "PENDING",
            "assigned_agent": "hr-agent",
            "run_id": "run_002",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    # Both should be present — different runs
    assert "task-old" in task_ids
    assert "task-new" in task_ids
    assert len(merged) == 2


def test_merge_task_pool_no_supersession_for_done_tasks():
    """DONE tasks should never be evicted, even for the same agent+run."""
    existing = [
        {
            "task_id": "task-done",
            "description": "查联系人",
            "status": "DONE",
            "assigned_agent": "contacts-agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new",
            "description": "查联系人",
            "status": "PENDING",
            "assigned_agent": "contacts-agent",
            "run_id": "run_001",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    # DONE task must survive
    assert "task-done" in task_ids
    assert "task-new" in task_ids
    assert len(merged) == 2


def test_merge_task_pool_supersedes_failed_by_running_replacement():
    """A RUNNING replacement task (e.g. router directly dispatches) should also
    evict the old FAILED task for the same agent+run."""
    existing = [
        {
            "task_id": "task-old",
            "description": "查考勤",
            "status": "FAILED",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new",
            "description": "查考勤",
            "status": "RUNNING",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    assert "task-old" not in task_ids
    assert "task-new" in task_ids
    assert len(merged) == 1


def test_merge_task_pool_supersession_case_insensitive_agent():
    """Agent name matching for supersession should be case-insensitive."""
    existing = [
        {
            "task_id": "task-old",
            "description": "查考勤",
            "status": "FAILED",
            "assigned_agent": "HR-Agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new",
            "description": "查考勤",
            "status": "PENDING",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    assert "task-old" not in task_ids
    assert "task-new" in task_ids


def test_merge_task_pool_supersedes_when_new_task_has_no_agent():
    """When the new PENDING task has no assigned_agent (LLM omitted it),
    it should still supersede a FAILED task in the same run."""
    existing = [
        {
            "task_id": "task-old",
            "description": "查考勤",
            "status": "FAILED",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new",
            "description": "查考勤",
            "status": "PENDING",
            "assigned_agent": None,
            "run_id": "run_001",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    # When either side has no agent, supersession still fires (same run)
    assert "task-old" not in task_ids
    assert "task-new" in task_ids


def test_merge_task_pool_no_supersession_when_agents_differ():
    """FAILED task for agent A should NOT be evicted by PENDING for agent B."""
    existing = [
        {
            "task_id": "task-old",
            "description": "查考勤",
            "status": "FAILED",
            "assigned_agent": "hr-agent",
            "run_id": "run_001",
        },
    ]
    new_tasks = [
        {
            "task_id": "task-new",
            "description": "查联系人",
            "status": "PENDING",
            "assigned_agent": "contacts-agent",
            "run_id": "run_001",
        }
    ]

    merged = merge_task_pool(existing, new_tasks)

    task_ids = {t["task_id"] for t in merged}
    assert "task-old" in task_ids
    assert "task-new" in task_ids
    assert len(merged) == 2


def test_merge_task_pool_keeps_status_transition_guard_while_merging_new_fields():
    existing = [
        {
            "task_id": "task-1",
            "description": "book meeting room",
            "status": "DONE",
        }
    ]
    updates = [
        {
            "task_id": "task-1",
            "status": "RUNNING",
            "continuation_mode": "continue_after_dependency",
            "pending_interrupt": {"interrupt_type": "dependency"},
            "agent_history_cutoff": 3,
        }
    ]

    merged = merge_task_pool(existing, updates)

    assert merged[0]["status"] == "DONE"
    assert merged[0]["continuation_mode"] == "continue_after_dependency"
    assert merged[0]["pending_interrupt"] == {"interrupt_type": "dependency"}
    assert merged[0]["agent_history_cutoff"] == 3
