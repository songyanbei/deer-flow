"""Regression tests for intervention-based clarification resume.

Covers three fix areas:
- 方案一: normalize_intervention_clarification_answer() and its integration
  into extract_latest_clarification_answer()
- 方案二: helper task context_payload preservation via resolved_inputs
- 方案三: redundant message-based fallback extraction
"""
from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.workflow_resume import (
    extract_intervention_clarification_from_message,
    extract_latest_clarification_answer,
    normalize_intervention_clarification_answer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_clarification_task(
    *,
    action_kind: str = "input",
    payload: dict | None = None,
    category: str = "user_clarification",
    questions: list[dict] | None = None,
    continuation_mode: str = "continue_after_intervention",
    resolution_behavior: str = "resume_current_task",
    has_resolution: bool = True,
) -> dict:
    """Build a minimal task with user_clarification intervention."""
    action: dict = {
        "key": "submit_response",
        "label": "提交回复",
        "kind": action_kind,
        "resolution_behavior": "resume_current_task",
    }
    task: dict = {
        "task_id": "task-uc",
        "description": "resolve employee openId",
        "status": "RUNNING",
        "continuation_mode": continuation_mode,
        "intervention_status": "resolved",
        "intervention_request": {
            "request_id": "req-uc",
            "fingerprint": "fp-uc",
            "source_signal": "request_help",
            "intervention_type": "clarification",
            "category": category,
            "source_agent": "contacts-agent",
            "source_task_id": "task-uc",
            "action_schema": {"actions": [action]},
            "questions": questions,
            "created_at": "2026-03-25T00:00:00Z",
            "title": "需要您的姓名",
            "reason": "查找 openId",
        },
    }
    if has_resolution:
        task["intervention_resolution"] = {
            "request_id": "req-uc",
            "fingerprint": "fp-uc",
            "action_key": "submit_response",
            "payload": payload or {},
            "resolution_behavior": resolution_behavior,
        }
    return task


def _state_with_task(task: dict, messages: list | None = None) -> dict:
    return {
        "task_pool": [task],
        "messages": messages or [],
    }


# ===================================================================
# 方案一: normalize_intervention_clarification_answer
# ===================================================================


class TestNormalizeInputKind:
    def test_text_field(self):
        task = _user_clarification_task(action_kind="input", payload={"text": "孙琦"})
        assert normalize_intervention_clarification_answer(task) == "孙琦"

    def test_comment_fallback(self):
        task = _user_clarification_task(action_kind="input", payload={"comment": "孙琦"})
        assert normalize_intervention_clarification_answer(task) == "孙琦"

    def test_text_preferred_over_comment(self):
        task = _user_clarification_task(action_kind="input", payload={"text": "A", "comment": "B"})
        assert normalize_intervention_clarification_answer(task) == "A"

    def test_empty_payload(self):
        task = _user_clarification_task(action_kind="input", payload={})
        assert normalize_intervention_clarification_answer(task) == ""


class TestNormalizeSingleSelectKind:
    def test_selected_value(self):
        task = _user_clarification_task(action_kind="single_select", payload={"selected": "选项A"})
        assert normalize_intervention_clarification_answer(task) == "选项A"

    def test_selected_with_custom_text(self):
        task = _user_clarification_task(action_kind="single_select", payload={"selected": "其他", "custom_text": "自定义内容"})
        assert normalize_intervention_clarification_answer(task) == "其他, 自定义内容"

    def test_select_alias(self):
        task = _user_clarification_task(action_kind="select", payload={"selected": "选项B"})
        assert normalize_intervention_clarification_answer(task) == "选项B"


class TestNormalizeMultiSelectKind:
    def test_list_selection(self):
        task = _user_clarification_task(action_kind="multi_select", payload={"selected": ["A", "B", "C"]})
        assert normalize_intervention_clarification_answer(task) == "A, B, C"

    def test_empty_selection(self):
        task = _user_clarification_task(action_kind="multi_select", payload={"selected": []})
        assert normalize_intervention_clarification_answer(task) == ""


class TestNormalizeConfirmKind:
    def test_confirmed(self):
        task = _user_clarification_task(action_kind="confirm", payload={"confirmed": True})
        assert normalize_intervention_clarification_answer(task) == "confirmed"


class TestNormalizeCompositeKind:
    def test_expand_questions(self):
        questions = [
            {"key": "q1", "label": "您的姓名", "kind": "input"},
            {"key": "q2", "label": "所在城市", "kind": "single_select"},
        ]
        payload = {
            "q1": {"text": "孙琦"},
            "q2": {"selected": "北京"},
        }
        task = _user_clarification_task(action_kind="composite", payload=payload, questions=questions)
        result = normalize_intervention_clarification_answer(task)
        assert "您的姓名: 孙琦" in result
        assert "所在城市: 北京" in result

    def test_string_values(self):
        questions = [
            {"key": "name", "label": "姓名", "kind": "input"},
        ]
        payload = {"name": "孙琦"}
        task = _user_clarification_task(action_kind="composite", payload=payload, questions=questions)
        result = normalize_intervention_clarification_answer(task)
        assert "姓名: 孙琦" in result

    def test_missing_questions_fallback(self):
        """composite with no questions list falls back to input extraction."""
        task = _user_clarification_task(action_kind="composite", payload={"text": "fallback"}, questions=None)
        assert normalize_intervention_clarification_answer(task) == "fallback"


class TestNormalizeGuardConditions:
    def test_skips_non_user_clarification(self):
        task = _user_clarification_task(category="before_tool")
        assert normalize_intervention_clarification_answer(task) == ""

    def test_skips_wrong_continuation_mode(self):
        task = _user_clarification_task(continuation_mode="resume_tool_call")
        assert normalize_intervention_clarification_answer(task) == ""

    def test_skips_missing_resolution(self):
        task = _user_clarification_task(has_resolution=False)
        assert normalize_intervention_clarification_answer(task) == ""

    def test_skips_fail_behavior(self):
        task = _user_clarification_task(resolution_behavior="fail_current_task")
        assert normalize_intervention_clarification_answer(task) == ""

    def test_skips_missing_intervention_request(self):
        task = _user_clarification_task()
        del task["intervention_request"]
        assert normalize_intervention_clarification_answer(task) == ""


# ===================================================================
# 方案一: extract_latest_clarification_answer integration
# ===================================================================


class TestExtractPrefersInterventionResolution:
    def test_intervention_resolution_takes_priority(self):
        task = _user_clarification_task(action_kind="input", payload={"text": "孙琦"})
        state = _state_with_task(task, messages=[HumanMessage(content="some old message")])
        result = extract_latest_clarification_answer(state)
        assert result == "孙琦"

    def test_falls_back_to_legacy_when_no_intervention(self):
        task = {
            "task_id": "task-legacy",
            "description": "legacy task",
            "status": "RUNNING",
            "clarification_prompt": "请问您的名字是？",
            "continuation_mode": "continue_after_clarification",
        }
        ask_msg = AIMessage(content="请问您的名字是？", name="ask_clarification")
        user_msg = HumanMessage(content="孙琦")
        state = _state_with_task(task, messages=[ask_msg, user_msg])
        result = extract_latest_clarification_answer(state)
        assert result == "孙琦"


# ===================================================================
# 方案二: helper task context_payload preservation
# ===================================================================


class TestHelperTaskUpstreamContext:
    def test_carries_context_payload(self):
        """_route_to_helper should inject context_payload into resolved_inputs."""
        # Import and patch only what we need to avoid heavy dependencies
        from src.agents.router.semantic_router import _route_to_helper

        parent_task = {
            "task_id": "parent-1",
            "description": "book meeting",
            "status": "WAITING_DEPENDENCY",
            "assigned_agent": "meeting-agent",
            "requested_by_agent": "meeting-agent",
            "request_help": {
                "problem": "需要查找孙琦的openId",
                "required_capability": "employee_lookup",
                "reason": "meeting booking requires organizer openId",
                "expected_output": "openId string",
                "context_payload": {"organizer_name": "孙琦", "department": "产品部"},
            },
            "help_depth": 0,
        }
        state = {"task_pool": [parent_task], "messages": [], "run_id": "run-1"}
        result = _route_to_helper(parent_task, state, route_count=1, assigned="contacts-agent")

        helper_task = [t for t in result["task_pool"] if t["task_id"] != "parent-1"][0]
        resolved = helper_task.get("resolved_inputs") or {}
        assert "upstream_context" in resolved
        assert resolved["upstream_context"]["organizer_name"] == "孙琦"

    def test_caps_oversized_payload(self):
        """context_payload exceeding size limit should NOT be injected."""
        from src.agents.router.semantic_router import _route_to_helper

        large_payload = {"data": "x" * 3000}
        parent_task = {
            "task_id": "parent-2",
            "description": "book meeting",
            "status": "WAITING_DEPENDENCY",
            "assigned_agent": "meeting-agent",
            "requested_by_agent": "meeting-agent",
            "request_help": {
                "problem": "需要查找员工",
                "required_capability": "employee_lookup",
                "reason": "reason",
                "expected_output": "openId",
                "context_payload": large_payload,
            },
            "help_depth": 0,
        }
        state = {"task_pool": [parent_task], "messages": [], "run_id": "run-2"}
        result = _route_to_helper(parent_task, state, route_count=1, assigned="contacts-agent")

        helper_task = [t for t in result["task_pool"] if t["task_id"] != "parent-2"][0]
        assert not (helper_task.get("resolved_inputs") or {}).get("upstream_context")

    def test_no_payload_no_injection(self):
        """No context_payload → no resolved_inputs on helper task."""
        from src.agents.router.semantic_router import _route_to_helper

        parent_task = {
            "task_id": "parent-3",
            "description": "book meeting",
            "status": "WAITING_DEPENDENCY",
            "assigned_agent": "meeting-agent",
            "requested_by_agent": "meeting-agent",
            "request_help": {
                "problem": "需要查找员工",
                "required_capability": "employee_lookup",
                "reason": "reason",
                "expected_output": "openId",
            },
            "help_depth": 0,
        }
        state = {"task_pool": [parent_task], "messages": [], "run_id": "run-3"}
        result = _route_to_helper(parent_task, state, route_count=1, assigned="contacts-agent")

        helper_task = [t for t in result["task_pool"] if t["task_id"] != "parent-3"][0]
        assert not helper_task.get("resolved_inputs")


# ===================================================================
# 方案三: message-based fallback extraction
# ===================================================================


class TestMessageFallbackExtraction:
    def test_json_answer(self):
        msg = HumanMessage(content='[intervention_resolved] {"answer": "孙琦"}')
        state = {"messages": [msg], "task_pool": []}
        assert extract_intervention_clarification_from_message(state) == "孙琦"

    def test_json_text_key(self):
        msg = HumanMessage(content='[intervention_resolved] {"text": "北京"}')
        state = {"messages": [msg], "task_pool": []}
        assert extract_intervention_clarification_from_message(state) == "北京"

    def test_plain_text_ignored(self):
        """Plain text after prefix is NOT treated as answer (avoids false positives)."""
        msg = HumanMessage(content="[intervention_resolved] some random text")
        state = {"messages": [msg], "task_pool": []}
        assert extract_intervention_clarification_from_message(state) == ""

    def test_empty_remainder(self):
        msg = HumanMessage(content="[intervention_resolved]")
        state = {"messages": [msg], "task_pool": []}
        assert extract_intervention_clarification_from_message(state) == ""

    def test_no_intervention_message(self):
        msg = HumanMessage(content="孙琦")
        state = {"messages": [msg], "task_pool": []}
        assert extract_intervention_clarification_from_message(state) == ""

    def test_fallback_not_used_when_primary_succeeds(self):
        """When intervention_resolution exists, message fallback is not the source."""
        task = _user_clarification_task(action_kind="input", payload={"text": "正确答案"})
        msg = HumanMessage(content='[intervention_resolved] {"answer": "错误来源"}')
        state = _state_with_task(task, messages=[msg])
        # extract_latest_clarification_answer should use the primary path
        result = extract_latest_clarification_answer(state)
        assert result == "正确答案"
