from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from src.agents.executor.outcome import normalize_agent_outcome


def _task() -> dict:
    return {
        "task_id": "task-1",
        "description": "book meeting room",
        "status": "RUNNING",
    }


def test_normalize_agent_outcome_ignores_replayed_request_help_when_current_round_completes():
    messages = [
        AIMessage(content="", tool_calls=[{"id": "old-help", "name": "request_help", "args": {}}]),
        ToolMessage(
            name="request_help",
            tool_call_id="old-help",
            content='{"problem":"missing room","required_capability":"lookup","reason":"need room","expected_output":"room"}',
        ),
        AIMessage(content="", tool_calls=[{"id": "done-1", "name": "task_complete", "args": {}}]),
        ToolMessage(
            name="task_complete",
            tool_call_id="done-1",
            content='{"result_text":"Booked successfully","fact_payload":{"status":"booked"}}',
        ),
        AIMessage(content="Booked successfully"),
    ]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=2)

    assert used_fallback is False
    assert outcome["kind"] == "complete"
    assert outcome["result_text"] == "Booked successfully"
    assert outcome["fact_payload"] == {"status": "booked"}


def test_normalize_agent_outcome_ignores_replayed_clarification_when_current_round_completes():
    messages = [
        ToolMessage(name="ask_clarification", tool_call_id="old-clarify", content="Which room do you want?"),
        AIMessage(content="", tool_calls=[{"id": "done-1", "name": "task_complete", "args": {}}]),
        ToolMessage(name="task_complete", tool_call_id="done-1", content='{"result_text":"done"}'),
    ]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=1)

    assert used_fallback is False
    assert outcome["kind"] == "complete"


def test_normalize_agent_outcome_ignores_replayed_intervention_when_current_round_completes():
    messages = [
        AIMessage(content="", tool_calls=[{"id": "tc-old", "name": "meeting_createMeeting", "args": {"roomId": "A"}}]),
        ToolMessage(
            name="intervention_required",
            tool_call_id="tc-old",
            content='{"request_id":"req-old","fingerprint":"fp-old"}',
        ),
        AIMessage(content="", tool_calls=[{"id": "done-1", "name": "task_complete", "args": {}}]),
        ToolMessage(name="task_complete", tool_call_id="done-1", content='{"result_text":"created"}'),
    ]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=2)

    assert used_fallback is False
    assert outcome["kind"] == "complete"


def test_normalize_agent_outcome_classifies_each_explicit_terminal_tool():
    cases = [
        ("task_complete", '{"result_text":"done"}', "complete"),
        ("task_fail", '{"error_message":"boom","retryable":true}', "fail"),
        (
            "request_help",
            '{"problem":"p","required_capability":"c","reason":"r","expected_output":"o"}',
            "request_dependency",
        ),
        ("ask_clarification", "Please choose a room", "request_clarification"),
        ("intervention_required", '{"request_id":"req-1","fingerprint":"fp-1"}', "request_intervention"),
    ]

    for tool_name, payload, expected_kind in cases:
        messages = [ToolMessage(name=tool_name, tool_call_id=f"{tool_name}-1", content=payload)]
        outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)
        assert used_fallback is False
        assert outcome["kind"] == expected_kind


def test_normalize_agent_outcome_falls_back_to_plain_text_completion_for_legacy_agent():
    messages = [AIMessage(content="The meeting room has been booked successfully.")]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is True
    assert outcome["kind"] == "complete"
    assert outcome["result_text"] == "The meeting room has been booked successfully."


def test_normalize_agent_outcome_falls_back_to_plain_text_clarification_for_legacy_agent():
    messages = [AIMessage(content="Please choose one room:\n1. Room A\n2. Room B")]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is True
    assert outcome["kind"] == "request_clarification"
    assert outcome["prompt"].startswith("Please choose one room")

