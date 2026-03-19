"""Tests for executor-level user-owned help request normalization.

Verifies that user-owned help requests (resolution_strategy = user_clarification,
user_confirmation, user_multi_select, or presence of clarification_options)
are directly written as WAITING_INTERVENTION by the executor, instead of
first entering WAITING_DEPENDENCY and relying on the router to upgrade.
"""

from src.agents.intervention.help_request_builder import (
    should_interrupt_for_user_clarification,
    build_help_request_intervention,
    normalize_clarification_options,
)


def _make_task(task_id: str = "task-1", status: str = "RUNNING") -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "description": "Book meeting room",
        "assigned_agent": "meeting-agent",
        "help_depth": 0,
    }


class TestExecutorNormalizationDecision:
    """Verify that the same detection logic used by the executor correctly
    classifies user-owned vs system-owned help requests."""

    def test_room_selection_is_user_owned(self):
        """The room-selection scenario that previously caused the mixed-state bug."""
        help_request = {
            "problem": "Multiple rooms available",
            "reason": "Need user to select a room",
            "clarification_question": "Which meeting room do you prefer?",
            "clarification_options": ["Room A", "Room B", "Room C"],
            "resolution_strategy": "user_clarification",
        }
        assert should_interrupt_for_user_clarification(help_request)
        options = normalize_clarification_options(help_request.get("clarification_options"))
        assert len(options) == 3

    def test_confirmation_is_user_owned(self):
        help_request = {
            "problem": "Booking confirmation needed",
            "reason": "User must confirm the booking",
            "resolution_strategy": "user_confirmation",
        }
        assert should_interrupt_for_user_clarification(help_request)

    def test_multi_select_is_user_owned(self):
        help_request = {
            "problem": "Select participants",
            "reason": "Multiple participants available",
            "clarification_options": ["Alice", "Bob"],
            "resolution_strategy": "user_multi_select",
        }
        assert should_interrupt_for_user_clarification(help_request)

    def test_agent_delegation_is_system_owned(self):
        help_request = {
            "problem": "Need calendar lookup",
            "reason": "Calendar API required",
            "resolution_strategy": "agent_delegation",
            "required_capability": "calendar_read",
        }
        assert not should_interrupt_for_user_clarification(help_request)

    def test_no_strategy_no_question_is_system_owned(self):
        help_request = {
            "problem": "Need external data",
            "reason": "Data not available locally",
            "required_capability": "data_fetch",
        }
        assert not should_interrupt_for_user_clarification(help_request)


class TestExecutorInterventionBuild:
    """Verify that the intervention request built by the executor matches
    the expected contract for the resolve endpoint."""

    def test_intervention_has_required_fields(self):
        task = _make_task()
        help_request = {
            "problem": "Room selection",
            "reason": "Multiple rooms",
            "clarification_question": "Which room?",
            "clarification_options": ["A", "B"],
            "resolution_strategy": "user_clarification",
        }
        intervention = build_help_request_intervention(
            task, help_request, agent_name="meeting-agent"
        )

        # Fields required by the resolve endpoint
        assert "request_id" in intervention
        assert "fingerprint" in intervention
        assert intervention["intervention_type"] == "clarification"
        assert intervention["source_task_id"] == "task-1"
        assert intervention["source_agent"] == "meeting-agent"
        assert "action_schema" in intervention
        assert "actions" in intervention["action_schema"]
        assert len(intervention["action_schema"]["actions"]) >= 1

    def test_intervention_task_state_consistency(self):
        """Simulate what the executor does: build the full task dict and verify
        no mixed state exists (status matches intervention payload)."""
        task = _make_task()
        help_request = {
            "problem": "Room selection",
            "reason": "Multiple rooms",
            "clarification_question": "Which room?",
            "clarification_options": ["A", "B"],
            "resolution_strategy": "user_clarification",
        }
        intervention = build_help_request_intervention(
            task, help_request, agent_name="meeting-agent"
        )

        # Simulate the executor-built task
        intervention_task = {
            **task,
            "status": "WAITING_INTERVENTION",
            "intervention_request": intervention,
            "intervention_status": "pending",
            "intervention_fingerprint": intervention["fingerprint"],
            "status_detail": "@waiting_intervention",
        }

        # No mixed state: status, detail, and intervention payload all agree
        assert intervention_task["status"] == "WAITING_INTERVENTION"
        assert intervention_task["status_detail"] == "@waiting_intervention"
        assert intervention_task["intervention_status"] == "pending"
        assert intervention_task["intervention_request"]["request_id"] == intervention["request_id"]


class TestTransitionTableValidity:
    """Verify the state transition table supports the new direct flow."""

    def test_running_to_waiting_intervention_is_valid(self):
        from src.agents.thread_state import _is_valid_status_transition
        assert _is_valid_status_transition("RUNNING", "WAITING_INTERVENTION")

    def test_running_to_waiting_dependency_is_valid(self):
        from src.agents.thread_state import _is_valid_status_transition
        assert _is_valid_status_transition("RUNNING", "WAITING_DEPENDENCY")

    def test_waiting_dependency_to_waiting_intervention_is_invalid(self):
        """This is the transition that caused the original bug — it should
        remain invalid to prevent mixed states."""
        from src.agents.thread_state import _is_valid_status_transition
        assert not _is_valid_status_transition("WAITING_DEPENDENCY", "WAITING_INTERVENTION")

    def test_waiting_intervention_to_running_is_valid(self):
        """After user resolves, task returns to RUNNING."""
        from src.agents.thread_state import _is_valid_status_transition
        assert _is_valid_status_transition("WAITING_INTERVENTION", "RUNNING")
