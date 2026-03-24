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


def test_normalize_agent_outcome_treats_substantial_result_with_trailing_question_as_complete():
    """When agent output is a long result with a trailing optional follow-up
    question, it should be classified as complete, not clarification."""
    # Simulate: HR agent returns 200+ chars of attendance data, then adds
    # an optional "如需查看具体异常日期，请告诉我"
    body = "以下是孙琦2026年3月考勤汇总：\n\n" + "出勤天数：20天\n缺勤天数：1天\n迟到：0次\n早退：0次\n" * 8
    trailing = "\n\n如需查看具体的异常日期或需要处理漏打卡补签，请告诉我。"
    full_output = body + trailing
    assert len(body) > 200  # Precondition: body is substantial

    messages = [AIMessage(content=full_output)]
    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is True
    assert outcome["kind"] == "complete"
    assert outcome["result_text"] == full_output


def test_normalize_agent_outcome_short_question_still_classified_as_clarification():
    """A short question without substantial preceding content should still be
    classified as clarification (not affected by the trailing follow-up check)."""
    messages = [AIMessage(content="请问您要查哪个月的考勤？")]
    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is True
    assert outcome["kind"] == "request_clarification"


def test_normalize_agent_outcome_falls_back_to_plain_text_clarification_for_legacy_agent():
    messages = [AIMessage(content="Please choose one room:\n1. Room A\n2. Room B")]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is True
    assert outcome["kind"] == "request_clarification"
    assert outcome["prompt"].startswith("Please choose one room")


def test_normalize_agent_outcome_prioritizes_intervention_over_followup_request_help():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-1", "name": "meeting_createMeeting", "args": {"roomId": "A"}},
            ],
        ),
        ToolMessage(
            name="intervention_required",
            tool_call_id="tc-1",
            content='{"request_id":"req-1","fingerprint":"fp-1","intervention_type":"before_tool","title":"approve","reason":"r","source_agent":"meeting-agent","source_task_id":"task-1","action_schema":{"actions":[{"key":"approve","resolution_behavior":"resume_current_task"}]},"created_at":"2026-03-19T00:00:00+00:00"}',
        ),
        ToolMessage(
            name="request_help",
            tool_call_id="help-1",
            content='{"problem":"need confirmation","required_capability":"user input","reason":"need user","expected_output":"decision","resolution_strategy":"user_confirmation"}',
        ),
    ]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is False
    assert outcome["kind"] == "request_intervention"
    assert outcome["selected_signal"] == "intervention_required"
    assert "request_help_user" in outcome["suppressed_signals"]


def test_normalize_agent_outcome_prioritizes_intervention_over_ask_clarification():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-1", "name": "meeting_createMeeting", "args": {"roomId": "A"}},
            ],
        ),
        ToolMessage(
            name="intervention_required",
            tool_call_id="tc-1",
            content='{"request_id":"req-1","fingerprint":"fp-1","intervention_type":"before_tool","title":"approve","reason":"r","source_agent":"meeting-agent","source_task_id":"task-1","action_schema":{"actions":[{"key":"approve","resolution_behavior":"resume_current_task"}]},"created_at":"2026-03-19T00:00:00+00:00"}',
        ),
        ToolMessage(
            name="ask_clarification",
            tool_call_id="clarify-1",
            content="Which room should I use?",
        ),
    ]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is False
    assert outcome["kind"] == "request_intervention"
    assert outcome["selected_signal"] == "intervention_required"
    assert "ask_clarification" in outcome["suppressed_signals"]


def test_normalize_agent_outcome_selects_single_authoritative_intervention_from_duplicates():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-1", "name": "meeting_createMeeting", "args": {"roomId": "A"}},
            ],
        ),
        ToolMessage(
            name="intervention_required",
            tool_call_id="tc-1",
            content='{"request_id":"req-1","fingerprint":"fp-1","intervention_type":"before_tool","title":"approve","reason":"r","source_agent":"meeting-agent","source_task_id":"task-1","action_schema":{"actions":[{"key":"approve","resolution_behavior":"resume_current_task"}]},"created_at":"2026-03-19T00:00:00+00:00"}',
        ),
        ToolMessage(
            name="intervention_required",
            tool_call_id="tc-1",
            content='{"request_id":"req-2","fingerprint":"fp-1","intervention_type":"before_tool","title":"approve-again","reason":"r","source_agent":"meeting-agent","source_task_id":"task-1","action_schema":{"actions":[{"key":"approve","resolution_behavior":"resume_current_task"}]},"created_at":"2026-03-19T00:00:01+00:00"}',
        ),
    ]

    outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)

    assert used_fallback is False
    assert outcome["kind"] == "request_intervention"
    assert outcome["intervention_request"]["request_id"] == "req-1"
    assert outcome["intervention_request"]["fingerprint"] == "fp-1"
    assert outcome["suppressed_signals"].count("intervention_required") == 1
