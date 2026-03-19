import asyncio
import json
from unittest.mock import patch

from langchain_core.messages import HumanMessage, ToolMessage

from src.agents.executor.executor import executor_node
from src.agents.graph import route_after_workflow_executor
from src.agents.intervention.decision_cache import (
    build_cached_intervention_entry,
    is_intervention_cache_valid,
)
from src.agents.intervention.fingerprint import (
    generate_clarification_semantic_fingerprint,
    generate_tool_semantic_fingerprint,
)
from src.agents.router.semantic_router import router_node
from src.agents.thread_state import merge_intervention_cache


def test_merge_intervention_cache_combines_entries():
    existing = {"fp-a": {"reuse_count": 1}}
    update = {"fp-b": {"reuse_count": 0}}

    result = merge_intervention_cache(existing, update)

    assert result == {
        "fp-a": {"reuse_count": 1},
        "fp-b": {"reuse_count": 0},
    }


def test_merge_intervention_cache_overwrites_same_key():
    result = merge_intervention_cache(
        {"fp-a": {"reuse_count": 1, "action_key": "approve"}},
        {"fp-a": {"reuse_count": 2, "action_key": "approve"}},
    )

    assert result["fp-a"]["reuse_count"] == 2


def test_build_cached_intervention_entry_for_tool():
    intervention_request = {
        "fingerprint": "runtime-fp",
        "intervention_type": "before_tool",
        "source_agent": "meeting-agent",
        "tool_name": "book_room",
        "context": {
            "tool_args": {"room": "A301"},
        },
    }

    semantic_fp, entry = build_cached_intervention_entry(
        intervention_request,
        action_key="approve",
        payload={"comment": "ok"},
        resolution_behavior="resume_current_task",
        resolved_at="2026-03-19T00:00:00+00:00",
    )

    assert semantic_fp == generate_tool_semantic_fingerprint("meeting-agent", "book_room", {"room": "A301"})
    assert entry["max_reuse"] == 3
    assert entry["reuse_count"] == 0


def test_build_cached_intervention_entry_for_input_clarification():
    semantic_fp = generate_clarification_semantic_fingerprint(
        "meeting-agent",
        "What topic should I use?",
        [],
    )
    intervention_request = {
        "fingerprint": semantic_fp,
        "intervention_type": "clarification",
        "source_agent": "meeting-agent",
    }

    cache_key, entry = build_cached_intervention_entry(
        intervention_request,
        action_key="submit_response",
        payload={"text": "Quarterly planning"},
        resolution_behavior="resume_current_task",
        resolved_at="2026-03-19T00:00:00+00:00",
    )

    assert cache_key == semantic_fp
    assert entry["max_reuse"] == -1
    assert entry["reuse_count"] == 0


def test_reject_cache_is_not_reusable_for_tool_interventions():
    assert not is_intervention_cache_valid(
        {
            "action_key": "reject",
            "payload": {"comment": "stop"},
            "resolution_behavior": "fail_current_task",
            "resolved_at": "2026-03-19T00:00:00+00:00",
            "intervention_type": "before_tool",
            "source_agent": "meeting-agent",
            "max_reuse": 3,
            "reuse_count": 0,
        },
        require_resume_behavior=True,
    )


def test_executor_auto_resumes_user_clarification_from_cache():
    class StubAgent:
        async def ainvoke(self, _payload, config=None):
            _ = config
            return {
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "problem": "Need room selection",
                                "required_capability": "room selection",
                                "reason": "Need the user to choose a room",
                                "expected_output": "selected room",
                                "resolution_strategy": "user_clarification",
                                "clarification_question": "Which room should I book?",
                                "clarification_options": ["Room A", "Room B"],
                            }
                        ),
                        tool_call_id="help-1",
                        name="request_help",
                    )
                ]
            }

    async def _run():
        semantic_fp = generate_clarification_semantic_fingerprint(
            "meeting-agent",
            "Which room should I book?",
            ["Room A", "Room B"],
        )
        intervention_cache = {
            semantic_fp: {
                "action_key": "submit_response",
                "payload": {"selected": "Room A"},
                "resolution_behavior": "resume_current_task",
                "resolved_at": "2026-03-19T00:00:00+00:00",
                "intervention_type": "clarification",
                "source_agent": "meeting-agent",
                "max_reuse": -1,
                "reuse_count": 0,
            }
        }
        state = {
            "run_id": "run-1",
            "task_pool": [
                {
                    "task_id": "task-1",
                    "description": "book meeting room",
                    "run_id": "run-1",
                    "assigned_agent": "meeting-agent",
                    "status": "RUNNING",
                    "help_depth": 0,
                }
            ],
            "verified_facts": {},
            "messages": [HumanMessage(content="Book a room")],
            "intervention_cache": intervention_cache,
        }

        with patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None):
            with patch("src.agents.lead_agent.agent.make_lead_agent", return_value=StubAgent()):
                result = await executor_node(state, {"configurable": {}})

        task = result["task_pool"][0]
        assert result["execution_state"] == "ROUTING_DONE"
        assert task["status"] == "RUNNING"
        assert task["status_detail"] == "@cache_auto_resolved"
        assert task["continuation_mode"] == "continue_after_clarification"
        assert task["resolved_inputs"]["intervention_resolution"]["payload"] == {"selected": "Room A"}
        assert result["intervention_cache"][semantic_fp]["reuse_count"] == 1

    asyncio.run(_run())


def test_router_compatibility_path_auto_resolves_from_cache():
    async def _run():
        semantic_fp = generate_clarification_semantic_fingerprint(
            "meeting-agent",
            "Which room should I book?",
            ["Room A", "Room B"],
        )
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "task-1",
                        "description": "book meeting room",
                        "run_id": "run-1",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 0,
                        "request_help": {
                            "problem": "Need room selection",
                            "required_capability": "room selection",
                            "reason": "Need the user to choose a room",
                            "expected_output": "selected room",
                            "resolution_strategy": "user_clarification",
                            "clarification_question": "Which room should I book?",
                            "clarification_options": ["Room A", "Room B"],
                        },
                    }
                ],
                "route_count": 0,
                "intervention_cache": {
                    semantic_fp: {
                        "action_key": "submit_response",
                        "payload": {"selected": "Room A"},
                        "resolution_behavior": "resume_current_task",
                        "resolved_at": "2026-03-19T00:00:00+00:00",
                        "intervention_type": "clarification",
                        "source_agent": "meeting-agent",
                        "max_reuse": -1,
                        "reuse_count": 0,
                    }
                },
            },
            {"configurable": {}},
        )

        task = result["task_pool"][0]
        assert result["execution_state"] == "ROUTING_DONE"
        assert task["status"] == "RUNNING"
        assert task["status_detail"] == "@cache_auto_resolved"
        assert task["continuation_mode"] == "continue_after_clarification"
        assert task["resolved_inputs"]["intervention_resolution"]["payload"] == {"selected": "Room A"}
        assert result["intervention_cache"][semantic_fp]["reuse_count"] == 1

    asyncio.run(_run())


def test_executor_auto_resumes_input_clarification_from_cache():
    class StubAgent:
        async def ainvoke(self, _payload, config=None):
            _ = config
            return {
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "problem": "Need a meeting topic",
                                "required_capability": "topic selection",
                                "reason": "Need the user to provide a topic",
                                "expected_output": "meeting topic",
                                "resolution_strategy": "user_clarification",
                                "clarification_question": "What topic should I use?",
                            }
                        ),
                        tool_call_id="help-1",
                        name="request_help",
                    )
                ]
            }

    async def _run():
        semantic_fp = generate_clarification_semantic_fingerprint(
            "meeting-agent",
            "What topic should I use?",
            [],
        )
        intervention_cache = {
            semantic_fp: {
                "action_key": "submit_response",
                "payload": {"text": "Quarterly planning"},
                "resolution_behavior": "resume_current_task",
                "resolved_at": "2026-03-19T00:00:00+00:00",
                "intervention_type": "clarification",
                "source_agent": "meeting-agent",
                "max_reuse": -1,
                "reuse_count": 0,
            }
        }
        state = {
            "run_id": "run-1",
            "task_pool": [
                {
                    "task_id": "task-1",
                    "description": "prepare agenda",
                    "run_id": "run-1",
                    "assigned_agent": "meeting-agent",
                    "status": "RUNNING",
                    "help_depth": 0,
                }
            ],
            "verified_facts": {},
            "messages": [HumanMessage(content="Prepare the agenda")],
            "intervention_cache": intervention_cache,
        }

        with patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None):
            with patch("src.agents.lead_agent.agent.make_lead_agent", return_value=StubAgent()):
                result = await executor_node(state, {"configurable": {}})

        task = result["task_pool"][0]
        assert result["execution_state"] == "ROUTING_DONE"
        assert task["status"] == "RUNNING"
        assert task["status_detail"] == "@cache_auto_resolved"
        assert task["continuation_mode"] == "continue_after_clarification"
        assert task["resolved_inputs"]["intervention_resolution"]["payload"] == {"text": "Quarterly planning"}
        assert result["intervention_cache"][semantic_fp]["reuse_count"] == 1

    asyncio.run(_run())


def test_router_compatibility_path_auto_resolves_input_clarification_from_cache():
    async def _run():
        semantic_fp = generate_clarification_semantic_fingerprint(
            "meeting-agent",
            "What topic should I use?",
            [],
        )
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "task-1",
                        "description": "prepare agenda",
                        "run_id": "run-1",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 0,
                        "request_help": {
                            "problem": "Need a meeting topic",
                            "required_capability": "topic selection",
                            "reason": "Need the user to provide a topic",
                            "expected_output": "meeting topic",
                            "resolution_strategy": "user_clarification",
                            "clarification_question": "What topic should I use?",
                        },
                    }
                ],
                "route_count": 0,
                "intervention_cache": {
                    semantic_fp: {
                        "action_key": "submit_response",
                        "payload": {"text": "Quarterly planning"},
                        "resolution_behavior": "resume_current_task",
                        "resolved_at": "2026-03-19T00:00:00+00:00",
                        "intervention_type": "clarification",
                        "source_agent": "meeting-agent",
                        "max_reuse": -1,
                        "reuse_count": 0,
                    }
                },
            },
            {"configurable": {}},
        )

        task = result["task_pool"][0]
        assert result["execution_state"] == "ROUTING_DONE"
        assert task["status"] == "RUNNING"
        assert task["status_detail"] == "@cache_auto_resolved"
        assert task["continuation_mode"] == "continue_after_clarification"
        assert task["resolved_inputs"]["intervention_resolution"]["payload"] == {"text": "Quarterly planning"}
        assert result["intervention_cache"][semantic_fp]["reuse_count"] == 1

    asyncio.run(_run())


def test_router_writes_cache_on_intervention_resolution():
    async def _run():
        semantic_fp = generate_clarification_semantic_fingerprint(
            "meeting-agent",
            "Which room should I book?",
            ["Room A", "Room B"],
        )
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "task-1",
                        "description": "book meeting room",
                        "run_id": "run-1",
                        "assigned_agent": "meeting-agent",
                        "status": "WAITING_INTERVENTION",
                        "intervention_status": "pending",
                        "intervention_request": {
                            "request_id": "intv-1",
                            "fingerprint": semantic_fp,
                            "intervention_type": "clarification",
                            "source_agent": "meeting-agent",
                            "action_schema": {
                                "actions": [
                                    {
                                        "key": "submit_response",
                                        "kind": "single_select",
                                        "resolution_behavior": "resume_current_task",
                                    }
                                ]
                            },
                        },
                    }
                ],
                "messages": [
                    HumanMessage(content="[intervention_resolved] request_id=intv-1 action_key=submit_response")
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "ROUTING_DONE"
        assert semantic_fp in result["intervention_cache"]
        assert result["intervention_cache"][semantic_fp]["intervention_type"] == "clarification"
        assert result["intervention_cache"][semantic_fp]["max_reuse"] == -1

    asyncio.run(_run())


def test_route_after_workflow_executor_routes_running_task_to_router():
    route = route_after_workflow_executor(
        {
            "execution_state": "ROUTING_DONE",
            "task_pool": [{"task_id": "task-1", "status": "RUNNING"}],
        }
    )

    assert route == "router"
