from __future__ import annotations

from src.agents.workflow_resume import (
    apply_intervention_resolution,
    build_intervention_resolution_record,
    resolve_intervention,
)


def _pending_task() -> dict:
    return {
        "task_id": "task-1",
        "description": "book meeting room",
        "status": "WAITING_INTERVENTION",
        "status_detail": "@waiting_intervention",
        "continuation_mode": "resume_tool_call",
        "intervention_status": "pending",
        "intervention_request": {
            "request_id": "req-1",
            "fingerprint": "fp-1",
            "semantic_key": "sem-1",
            "interrupt_kind": "before_tool",
            "source_signal": "intervention_required",
            "intervention_type": "before_tool",
            "source_agent": "meeting-agent",
            "source_task_id": "task-1",
            "action_schema": {
                "actions": [
                    {"key": "approve", "resolution_behavior": "resume_current_task"},
                    {"key": "reject", "resolution_behavior": "fail_current_task"},
                ]
            },
        },
        "pending_interrupt": {
            "interrupt_type": "intervention",
            "request_id": "req-1",
            "fingerprint": "fp-1",
            "interrupt_kind": "before_tool",
            "semantic_key": "sem-1",
            "source_signal": "intervention_required",
            "source_agent": "meeting-agent",
        },
        "pending_tool_call": {
            "tool_name": "meeting_createMeeting",
            "tool_args": {"roomId": "room-a"},
            "tool_call_id": "create-1",
            "snapshot_hash": "snap-1",
            "interrupt_fingerprint": "fp-1",
        },
        "resolved_inputs": {"dependency_result": {"roomId": "room-a"}},
    }


def test_apply_intervention_resolution_persists_full_resolution_context():
    resolution = build_intervention_resolution_record(
        request_id="req-1",
        fingerprint="fp-1",
        action_key="approve",
        payload={"comment": "ship it"},
        resolution_behavior="resume_current_task",
    )

    updated_task, error = apply_intervention_resolution(
        _pending_task(),
        resolution,
        resolved_at="2026-03-19T00:00:00+00:00",
    )

    assert error is None
    assert updated_task is not None
    assert updated_task["status"] == "RUNNING"
    assert updated_task["intervention_status"] == "resolved"
    assert updated_task["intervention_resolution"] == {
        "request_id": "req-1",
        "fingerprint": "fp-1",
        "action_key": "approve",
        "payload": {"comment": "ship it"},
        "resolution_behavior": "resume_current_task",
    }
    assert updated_task["resolved_inputs"]["intervention_resolution"] == updated_task["intervention_resolution"]
    assert updated_task["pending_interrupt"]["semantic_key"] == "sem-1"
    assert updated_task["pending_tool_call"]["interrupt_fingerprint"] == "fp-1"


def test_apply_intervention_resolution_rejects_request_id_mismatch():
    resolution = build_intervention_resolution_record(
        request_id="req-stale",
        fingerprint="fp-1",
        action_key="approve",
        payload={},
        resolution_behavior="resume_current_task",
    )

    updated_task, error = apply_intervention_resolution(_pending_task(), resolution)

    assert updated_task is None
    assert error == "request_id_mismatch"


def test_apply_intervention_resolution_rejects_fingerprint_mismatch():
    resolution = build_intervention_resolution_record(
        request_id="req-1",
        fingerprint="fp-stale",
        action_key="approve",
        payload={},
        resolution_behavior="resume_current_task",
    )

    updated_task, error = apply_intervention_resolution(_pending_task(), resolution)

    assert updated_task is None
    assert error == "fingerprint_mismatch"


def test_resolve_intervention_reads_latest_waiting_task_from_state():
    resolution = build_intervention_resolution_record(
        request_id="req-1",
        fingerprint="fp-1",
        action_key="approve",
        payload={"comment": "ok"},
        resolution_behavior="resume_current_task",
    )
    state = {
        "task_pool": [
            {"task_id": "done-1", "status": "DONE"},
            _pending_task(),
        ]
    }

    updated_task, error = resolve_intervention(state, resolution)

    assert error is None
    assert updated_task is not None
    assert updated_task["intervention_resolution"]["request_id"] == "req-1"
    assert updated_task["resolved_inputs"]["intervention_resolution"]["payload"] == {"comment": "ok"}
