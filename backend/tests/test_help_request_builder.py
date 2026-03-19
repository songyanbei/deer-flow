"""Tests for the shared help-request-to-intervention builder module."""

from src.agents.intervention.help_request_builder import (
    build_help_request_intervention,
    normalize_clarification_options,
    resolve_user_interaction_kind,
    should_interrupt_for_user_clarification,
)
from src.agents.intervention.fingerprint import generate_clarification_semantic_fingerprint


def _make_task(task_id: str = "task-1", status: str = "RUNNING") -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "description": "test task",
        "assigned_agent": "meeting-agent",
    }


# ---------------------------------------------------------------------------
# should_interrupt_for_user_clarification
# ---------------------------------------------------------------------------


def test_should_interrupt_user_clarification_strategy():
    assert should_interrupt_for_user_clarification({"resolution_strategy": "user_clarification"})


def test_should_interrupt_user_confirmation_strategy():
    assert should_interrupt_for_user_clarification({"resolution_strategy": "user_confirmation"})


def test_should_interrupt_user_multi_select_strategy():
    assert should_interrupt_for_user_clarification({"resolution_strategy": "user_multi_select"})


def test_should_interrupt_with_clarification_question():
    assert should_interrupt_for_user_clarification({"clarification_question": "Which room?"})


def test_should_not_interrupt_system_dependency():
    assert not should_interrupt_for_user_clarification({"resolution_strategy": "agent_delegation"})


def test_should_not_interrupt_empty_payload():
    assert not should_interrupt_for_user_clarification({})


def test_should_not_interrupt_blank_question():
    assert not should_interrupt_for_user_clarification({"clarification_question": "  "})


# ---------------------------------------------------------------------------
# normalize_clarification_options
# ---------------------------------------------------------------------------


def test_normalize_options_list():
    assert normalize_clarification_options(["Room A", " Room B ", "Room C"]) == ["Room A", "Room B", "Room C"]


def test_normalize_options_filters_blanks():
    assert normalize_clarification_options(["Room A", "", "  ", "Room B"]) == ["Room A", "Room B"]


def test_normalize_options_non_list():
    assert normalize_clarification_options(None) == []
    assert normalize_clarification_options("not a list") == []


# ---------------------------------------------------------------------------
# resolve_user_interaction_kind
# ---------------------------------------------------------------------------


def test_resolve_confirm():
    assert resolve_user_interaction_kind({"resolution_strategy": "user_confirmation"}, []) == "confirm"


def test_resolve_multi_select():
    assert resolve_user_interaction_kind({"resolution_strategy": "user_multi_select"}, []) == "multi_select"


def test_resolve_single_select_with_options():
    assert resolve_user_interaction_kind({}, ["A", "B"]) == "single_select"


def test_resolve_input_fallback():
    assert resolve_user_interaction_kind({}, []) == "input"


# ---------------------------------------------------------------------------
# build_help_request_intervention
# ---------------------------------------------------------------------------


def test_build_intervention_single_select():
    task = _make_task()
    help_request = {
        "problem": "Need room selection",
        "reason": "Multiple rooms available",
        "clarification_question": "Which meeting room?",
        "clarification_options": ["Room A", "Room B", "Room C"],
        "resolution_strategy": "user_clarification",
    }
    result = build_help_request_intervention(task, help_request, agent_name="meeting-agent")

    assert result["intervention_type"] == "clarification"
    assert result["source_task_id"] == "task-1"
    assert result["source_agent"] == "meeting-agent"
    assert result["category"] == "user_clarification"
    assert result["request_id"].startswith("intv_")
    assert len(result["fingerprint"]) == 24
    assert result["title"] == "Which meeting room?"

    actions = result["action_schema"]["actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["key"] == "submit_response"
    assert action["kind"] == "single_select"
    assert action["resolution_behavior"] == "resume_current_task"
    assert len(action["options"]) == 3
    assert action["min_select"] == 1
    assert action["max_select"] == 1


def test_build_intervention_confirm():
    task = _make_task()
    help_request = {
        "problem": "Confirm booking",
        "reason": "Need user confirmation",
        "clarification_question": "Confirm this booking?",
        "resolution_strategy": "user_confirmation",
    }
    result = build_help_request_intervention(task, help_request, agent_name="meeting-agent")

    actions = result["action_schema"]["actions"]
    assert actions[0]["kind"] == "confirm"


def test_build_intervention_multi_select():
    task = _make_task()
    help_request = {
        "problem": "Select participants",
        "reason": "Multiple participants",
        "clarification_question": "Select participants",
        "clarification_options": ["Alice", "Bob", "Charlie"],
        "resolution_strategy": "user_multi_select",
    }
    result = build_help_request_intervention(task, help_request, agent_name="meeting-agent")

    actions = result["action_schema"]["actions"]
    assert actions[0]["kind"] == "multi_select"
    assert actions[0]["max_select"] == 3


def test_build_intervention_input_fallback():
    task = _make_task()
    help_request = {
        "problem": "Need input",
        "reason": "Need custom input",
        "clarification_question": "Enter meeting topic",
        "resolution_strategy": "user_clarification",
    }
    result = build_help_request_intervention(task, help_request, agent_name="meeting-agent")

    actions = result["action_schema"]["actions"]
    assert actions[0]["kind"] == "input"
    assert actions[0]["placeholder"] == "Enter meeting topic"


def test_clarification_semantic_fingerprint_deterministic():
    fp1 = generate_clarification_semantic_fingerprint(
        "meeting-agent",
        "Which meeting room?",
        ["Room A", "Room B"],
    )
    fp2 = generate_clarification_semantic_fingerprint(
        "meeting-agent",
        "Which meeting room?",
        ["Room B", "Room A"],
    )
    assert fp1 == fp2


def test_clarification_semantic_fingerprint_deterministic_without_options():
    fp1 = generate_clarification_semantic_fingerprint(
        "meeting-agent",
        "What topic should I use?",
        [],
    )
    fp2 = generate_clarification_semantic_fingerprint(
        "meeting-agent",
        "  What topic should I use?  ",
        [],
    )
    assert fp1 == fp2


def test_build_intervention_uses_deterministic_fingerprint():
    task = _make_task()
    help_request = {
        "problem": "Need room selection",
        "reason": "Multiple rooms available",
        "clarification_question": "Which meeting room?",
        "clarification_options": ["Room A", "Room B"],
        "resolution_strategy": "user_clarification",
    }

    first = build_help_request_intervention(task, help_request, agent_name="meeting-agent")
    second = build_help_request_intervention(task, help_request, agent_name="meeting-agent")

    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"] == generate_clarification_semantic_fingerprint(
        "meeting-agent",
        "Which meeting room?",
        ["Room A", "Room B"],
    )
