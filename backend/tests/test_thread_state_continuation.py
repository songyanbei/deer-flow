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
