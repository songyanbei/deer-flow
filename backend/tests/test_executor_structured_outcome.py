from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.executor.executor import executor_node


def _base_task(**overrides):
    task = {
        "task_id": "task-1",
        "description": "book meeting room",
        "run_id": "run-1",
        "assigned_agent": "meeting-agent",
        "status": "RUNNING",
    }
    task.update(overrides)
    return task


def _run_executor(monkeypatch, agent, state):
    def _make_lead_agent(_config):
        return agent

    monkeypatch.setattr(
        "src.agents.executor.executor.load_agent_config",
        lambda _name: SimpleNamespace(mcp_servers=[], intervention_policies={}, hitl_keywords=[]),
    )

    async def _run():
        with patch("src.agents.executor.executor._ensure_mcp_ready", AsyncMock(return_value=None)):
            with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
                with patch("src.agents.lead_agent.agent.make_lead_agent", new=_make_lead_agent):
                    return await executor_node(state, {"configurable": {"thread_id": "thread-1"}})

    return asyncio.run(_run())


def test_executor_branches_to_waiting_dependency_from_structured_outcome(monkeypatch):
    class DependencyAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    AIMessage(content="", tool_calls=[{"id": "help-1", "name": "request_help", "args": {}}]),
                    ToolMessage(
                        name="request_help",
                        tool_call_id="help-1",
                        content=json.dumps(
                            {
                                "problem": "room id missing",
                                "required_capability": "room lookup",
                                "reason": "booking API requires room id",
                                "expected_output": "room id",
                            }
                        ),
                    ),
                ]
            }

    result = _run_executor(monkeypatch, DependencyAgent(), {"task_pool": [_base_task()], "verified_facts": {}})

    task = result["task_pool"][0]
    assert result["execution_state"] == "EXECUTING_DONE"
    assert task["status"] == "WAITING_DEPENDENCY"
    assert task["continuation_mode"] == "continue_after_dependency"
    assert task["pending_interrupt"]["interrupt_type"] == "dependency"


def test_executor_branches_to_waiting_clarification_from_structured_outcome(monkeypatch):
    class ClarificationAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        name="ask_clarification",
                        tool_call_id="clarify-1",
                        content="Which room should I choose?",
                    )
                ]
            }

    result = _run_executor(monkeypatch, ClarificationAgent(), {"task_pool": [_base_task()], "verified_facts": {}})

    task = result["task_pool"][0]
    assert result["execution_state"] == "INTERRUPTED"
    assert task["status"] == "RUNNING"
    assert task["status_detail"] == "@waiting_clarification"
    assert task["clarification_prompt"] == "Which room should I choose?"
    assert task["continuation_mode"] == "continue_after_clarification"
    assert task["pending_interrupt"]["interrupt_type"] == "clarification"


def test_executor_branches_to_waiting_intervention_from_structured_outcome(monkeypatch):
    class InterventionAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "create-1",
                                "name": "meeting_createMeeting",
                                "args": {"roomId": "room-a", "topic": "sync"},
                            }
                        ],
                    ),
                    ToolMessage(
                        name="intervention_required",
                        tool_call_id="create-1",
                        content=json.dumps(
                            {
                                "request_id": "req-1",
                                "fingerprint": "fp-1",
                                "semantic_key": "sem-1",
                                "interrupt_kind": "before_tool",
                                "source_signal": "intervention_required",
                                "intervention_type": "before_tool",
                                "title": "Confirm meeting creation",
                                "reason": "This tool has side effects",
                                "source_agent": "meeting-agent",
                                "source_task_id": "task-1",
                                "action_schema": {
                                    "actions": [
                                        {"key": "approve", "resolution_behavior": "resume_current_task"}
                                    ]
                                },
                                "created_at": "2026-03-19T00:00:00+00:00",
                                "context": {"idempotency_key": "idem-1"},
                            }
                        ),
                    ),
                ]
            }

    result = _run_executor(monkeypatch, InterventionAgent(), {"task_pool": [_base_task()], "verified_facts": {}})

    task = result["task_pool"][0]
    assert result["execution_state"] == "INTERRUPTED"
    assert task["status"] == "WAITING_INTERVENTION"
    assert task["continuation_mode"] == "resume_tool_call"
    assert task["pending_interrupt"]["interrupt_type"] == "intervention"
    assert task["pending_interrupt"]["interrupt_kind"] == "before_tool"
    assert task["pending_interrupt"]["semantic_key"] == "sem-1"
    assert task["pending_tool_call"]["tool_name"] == "meeting_createMeeting"
    assert task["pending_tool_call"]["snapshot_hash"]
    assert task["pending_tool_call"]["interrupt_fingerprint"] == "fp-1"


def test_executor_marks_done_and_clears_continuation_fields_on_task_complete(monkeypatch):
    class CompleteAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        name="task_complete",
                        tool_call_id="done-1",
                        content=json.dumps({"result_text": "Meeting booked", "fact_payload": {"status": "booked"}}),
                    )
                ]
            }

    result = _run_executor(
        monkeypatch,
        CompleteAgent(),
        {
            "task_pool": [
                _base_task(
                    continuation_mode="continue_after_dependency",
                    pending_interrupt={"interrupt_type": "dependency"},
                    pending_tool_call={"tool_name": "meeting_createMeeting", "tool_args": {}},
                )
            ],
            "verified_facts": {},
        },
    )

    task = result["task_pool"][0]
    assert task["status"] == "DONE"
    assert task["continuation_mode"] is None
    assert task["pending_interrupt"] is None
    assert task["pending_tool_call"] is None


def test_executor_marks_failed_and_clears_continuation_fields_on_task_fail(monkeypatch):
    class FailAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        name="task_fail",
                        tool_call_id="fail-1",
                        content=json.dumps({"error_message": "booking rejected", "retryable": False}),
                    )
                ]
            }

    result = _run_executor(
        monkeypatch,
        FailAgent(),
        {
            "task_pool": [
                _base_task(
                    continuation_mode="resume_tool_call",
                    pending_interrupt={"interrupt_type": "intervention"},
                    pending_tool_call={"tool_name": "meeting_createMeeting", "tool_args": {}},
                )
            ],
            "verified_facts": {},
        },
    )

    task = result["task_pool"][0]
    assert task["status"] == "FAILED"
    assert task["continuation_mode"] is None
    assert task["pending_interrupt"] is None
    assert task["pending_tool_call"] is None


def test_executor_legacy_intervention_resume_without_continuation_mode_uses_fallback(monkeypatch):
    class FollowupAgent:
        async def ainvoke(self, payload, **_kwargs):
            assert payload["messages"][-1].name == "meeting_createMeeting"
            return {"messages": [ToolMessage(name="task_complete", tool_call_id="done-1", content='{"result_text":"done"}')]}

    async def _execute_tool(stored_tool_call, _config):
        return ToolMessage(
            name=stored_tool_call["tool_name"],
            tool_call_id=stored_tool_call["tool_call_id"],
            content='{"status":"ok"}',
        )

    state = {
        "task_pool": [
            _base_task(
                status_detail="@intervention_resolved",
                intervention_status="resolved",
                continuation_mode=None,
                resolved_inputs={
                    "intervention_resolution": {
                        "request_id": "req-1",
                        "fingerprint": "fp-1",
                        "action_key": "approve",
                        "payload": {},
                        "resolution_behavior": "resume_current_task",
                    }
                },
                intercepted_tool_call={
                    "tool_name": "meeting_createMeeting",
                    "tool_args": {"roomId": "room-a"},
                    "tool_call_id": "create-1",
                },
                agent_messages=[
                    {"type": "human", "data": {"content": "book room"}},
                    {
                        "type": "ai",
                        "data": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "create-1",
                                    "name": "meeting_createMeeting",
                                    "args": {"roomId": "room-a"},
                                }
                            ],
                        },
                    },
                    {
                        "type": "tool",
                        "data": {
                            "content": '{"request_id":"req-1","fingerprint":"fp-1"}',
                            "tool_call_id": "create-1",
                            "name": "intervention_required",
                        },
                    },
                ],
            )
        ],
        "verified_facts": {},
        "messages": [HumanMessage(content="[intervention_resolved] request_id=req-1 action_key=approve")],
    }

    def _make_lead_agent(_config):
        return FollowupAgent()

    monkeypatch.setattr(
        "src.agents.executor.executor.load_agent_config",
        lambda _name: SimpleNamespace(mcp_servers=[], intervention_policies={}, hitl_keywords=[]),
    )

    async def _run():
            with patch("src.agents.executor.executor._ensure_mcp_ready", AsyncMock(return_value=None)):
                with patch("src.agents.executor.executor._execute_intercepted_tool_call", new=_execute_tool):
                    with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
                        with patch("src.agents.lead_agent.agent.make_lead_agent", new=_make_lead_agent):
                            return await executor_node(state, {"configurable": {"thread_id": "thread-1"}})

    result = asyncio.run(_run())
    task = result["task_pool"][0]
    assert task["status"] == "DONE"
    assert task["continuation_mode"] is None


def test_executor_does_not_reexecute_consumed_intervention(monkeypatch):
    class FollowupAgent:
        async def ainvoke(self, *_args, **_kwargs):
            raise AssertionError("agent should not run after an intervention is already consumed")

    execute_tool = AsyncMock(side_effect=AssertionError("tool should not be executed twice"))

    state = {
        "task_pool": [
            _base_task(
                status_detail="@intervention_resolved",
                intervention_status="consumed",
                continuation_mode="resume_tool_call",
                resolved_inputs={
                    "intervention_resolution": {
                        "request_id": "req-1",
                        "fingerprint": "fp-1",
                        "action_key": "approve",
                        "payload": {},
                        "resolution_behavior": "resume_current_task",
                    }
                },
                pending_tool_call={
                    "tool_name": "meeting_createMeeting",
                    "tool_args": {"roomId": "room-a"},
                    "tool_call_id": "create-1",
                    "snapshot_hash": "dont-care",
                    "interrupt_fingerprint": "fp-1",
                },
            )
        ],
        "verified_facts": {},
        "messages": [HumanMessage(content="[intervention_resolved] request_id=req-1 action_key=approve")],
    }

    def _make_lead_agent(_config):
        return FollowupAgent()

    monkeypatch.setattr(
        "src.agents.executor.executor.load_agent_config",
        lambda _name: SimpleNamespace(mcp_servers=[], intervention_policies={}, hitl_keywords=[]),
    )

    async def _run():
        with patch("src.agents.executor.executor._ensure_mcp_ready", AsyncMock(return_value=None)):
            with patch("src.agents.executor.executor._execute_intercepted_tool_call", new=execute_tool):
                with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
                    with patch("src.agents.lead_agent.agent.make_lead_agent", new=_make_lead_agent):
                        return await executor_node(state, {"configurable": {"thread_id": "thread-1"}})

    result = asyncio.run(_run())

    assert result["execution_state"] == "EXECUTING_DONE"
    assert result["task_pool"][0]["intervention_status"] == "consumed"
    execute_tool.assert_not_awaited()


def test_executor_rejects_resume_when_pending_tool_snapshot_drifts(monkeypatch):
    class FollowupAgent:
        async def ainvoke(self, *_args, **_kwargs):
            raise AssertionError("agent should not run when snapshot drift is detected")

    execute_tool = AsyncMock(side_effect=AssertionError("tool should not execute when snapshot drift is detected"))

    state = {
        "task_pool": [
            _base_task(
                status_detail="@intervention_resolved",
                intervention_status="resolved",
                continuation_mode="resume_tool_call",
                resolved_inputs={
                    "intervention_resolution": {
                        "request_id": "req-1",
                        "fingerprint": "fp-1",
                        "action_key": "approve",
                        "payload": {},
                        "resolution_behavior": "resume_current_task",
                    }
                },
                pending_tool_call={
                    "tool_name": "meeting_createMeeting",
                    "tool_args": {"roomId": "room-a"},
                    "tool_call_id": "create-1",
                    "snapshot_hash": "stale-snapshot",
                    "interrupt_fingerprint": "fp-1",
                },
            )
        ],
        "verified_facts": {},
        "messages": [HumanMessage(content="[intervention_resolved] request_id=req-1 action_key=approve")],
    }

    def _make_lead_agent(_config):
        return FollowupAgent()

    monkeypatch.setattr(
        "src.agents.executor.executor.load_agent_config",
        lambda _name: SimpleNamespace(mcp_servers=[], intervention_policies={}, hitl_keywords=[]),
    )

    async def _run():
        with patch("src.agents.executor.executor._ensure_mcp_ready", AsyncMock(return_value=None)):
            with patch("src.agents.executor.executor._execute_intercepted_tool_call", new=execute_tool):
                with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
                    with patch("src.agents.lead_agent.agent.make_lead_agent", new=_make_lead_agent):
                        return await executor_node(state, {"configurable": {"thread_id": "thread-1"}})

    result = asyncio.run(_run())

    task = result["task_pool"][0]
    assert task["status"] == "FAILED"
    assert "snapshot drift" in task["error"]
    execute_tool.assert_not_awaited()
