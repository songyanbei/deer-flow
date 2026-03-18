"""Pytest-collected integration test for the multi-agent graph."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from src.agents.executor.executor import SYSTEM_FALLBACK_FINAL_MESSAGE


class DummyResponse:
    def __init__(self, content):
        self.content = content


class PlannerLLM:
    def __init__(self):
        self.call_count = 0

    async def ainvoke(self, _messages):
        self.call_count += 1
        if self.call_count == 1:
            return DummyResponse(
                '[{"description": "lookup employee id", "assigned_agent": "contacts-agent"}, '
                '{"description": "lookup leave status", "assigned_agent": "hr-agent"}]'
            )
        return DummyResponse('{"done": true, "summary": "Employee ID is A-1001 and there is no active leave record."}')


class DomainAgentStub:
    def __init__(self):
        self.calls: dict[str, int] = {}

    async def ainvoke(self, payload, config=None):
        agent_name = config.get("configurable", {}).get("agent_name")
        self.calls[agent_name] = self.calls.get(agent_name, 0) + 1
        # On resume, prior messages are prepended; the new context is always the last HumanMessage
        all_messages = payload["messages"]
        context = next(
            (m.content for m in reversed(all_messages) if isinstance(m, HumanMessage)),
            all_messages[0].content,
        )

        if agent_name == "contacts-agent":
            if self.calls[agent_name] == 1:
                return {
                    "messages": [
                        ToolMessage(
                            content="Please confirm the employee identity, such as department or full name.",
                            tool_call_id="clarify-1",
                            name="ask_clarification",
                        )
                    ]
                }
            assert "User clarification answer:\nWang Mingtian from R&D" in context
            return {"messages": [AIMessage(content="Employee ID is A-1001")]}

        if agent_name == "hr-agent":
            assert "Known facts" in context
            return {"messages": [AIMessage(content="There is no active leave record")]}

        raise AssertionError(f"Unexpected agent: {agent_name}")


def make_lead_agent_stub(domain_stub):
    def _factory(_config):
        return domain_stub

    return _factory


def test_multi_agent_graph_end_to_end():
    async def _run():
        from src.agents.graph import build_multi_agent_graph_for_test

        planner_llm = PlannerLLM()
        domain_stub = DomainAgentStub()
        checkpointer = MemorySaver()
        events: list[dict] = []

        with ExitStack() as stack:
            stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
            stack.enter_context(
                patch(
                    "src.agents.router.semantic_router.list_domain_agents",
                    return_value=[
                        type("Agent", (), {"name": "contacts-agent", "description": "Lookup employees"})(),
                        type("Agent", (), {"name": "hr-agent", "description": "Lookup leave status"})(),
                    ],
                )
            )
            stack.enter_context(patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None))
            stack.enter_context(patch("src.agents.executor.executor.make_lead_agent", create=True, new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.lead_agent.agent.make_lead_agent", new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.executor.executor.get_stream_writer", return_value=events.append))

            graph = build_multi_agent_graph_for_test(checkpointer=checkpointer)
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

            first_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="Check whether Wang Mingtian is on leave and tell me the employee ID.")]},
                config=config,
            )

            assert first_state.get("execution_state") == "INTERRUPTED"
            assert first_state.get("run_id", "").startswith("run_")
            assert first_state.get("task_pool", [])[0]["status"] == "RUNNING"
            assert first_state.get("task_pool", [])[0]["run_id"] == first_state.get("run_id")
            assert first_state.get("task_pool", [])[0]["clarification_prompt"] == "Please confirm the employee identity, such as department or full name."
            assert first_state.get("planner_goal") == "Check whether Wang Mingtian is on leave and tell me the employee ID."
            assert [event["type"] for event in events[:3]] == ["task_started", "task_running", "task_running"]
            assert all(event["source"] == "multi_agent" for event in events[:3])
            assert all(event["run_id"] == first_state.get("run_id") for event in events[:3])
            assert events[2]["status"] == "waiting_clarification"

            final_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="Wang Mingtian from R&D")]},
                config=config,
            )

            assert final_state.get("execution_state") == "DONE"
            assert final_state.get("run_id") == first_state.get("run_id")
            assert final_state.get("final_result") == "Employee ID is A-1001 and there is no active leave record."
            assert final_state.get("planner_goal") == "Check whether Wang Mingtian is on leave and tell me the employee ID."
            assert final_state.get("workflow_stage") == "summarizing"
            assert final_state.get("workflow_stage_detail") == "Employee ID is A-1001 and there is no active leave record."
            assert len(final_state.get("verified_facts", {})) == 2
            assert all(t["status"] == "DONE" for t in final_state.get("task_pool", []))
            assert all(t["run_id"] == final_state.get("run_id") for t in final_state.get("task_pool", []))
            assert final_state.get("messages")[-1].content == "Employee ID is A-1001 and there is no active leave record."
            assert {event["type"] for event in events} >= {"task_started", "task_running", "task_completed"}
            assert len([event for event in events if event["type"] == "task_completed"]) == 2

    asyncio.run(_run())


def test_multi_agent_graph_request_help_round_trip():
    class PlannerLLMWithDependency:
        def __init__(self):
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            if self.call_count == 1:
                return DummyResponse('[{"description": "book a meeting room", "assigned_agent": "meeting-agent"}]')
            return DummyResponse('{"done": true, "summary": "Meeting booked for Wang Xing in Shanghai."}')

    class DomainAgentStubWithHelp:
        def __init__(self):
            self.calls: dict[str, int] = {}

        async def ainvoke(self, payload, config=None):
            agent_name = config.get("configurable", {}).get("agent_name")
            self.calls[agent_name] = self.calls.get(agent_name, 0) + 1
            # On resume, prior messages are prepended; the new context is always the last HumanMessage
            all_messages = payload["messages"]
            context = next(
                (m.content for m in reversed(all_messages) if isinstance(m, HumanMessage)),
                all_messages[0].content,
            )

            if agent_name == "meeting-agent":
                if self.calls[agent_name] == 1:
                    return {
                        "messages": [
                            ToolMessage(
                                content='{"problem":"Missing organizer openId","required_capability":"contact lookup","reason":"Meeting API requires organizer identity","expected_output":"Organizer openId and city","candidate_agents":["contacts-agent"]}',
                                tool_call_id="help-1",
                                name="request_help",
                            )
                        ]
                    }
                assert "Resolved dependency inputs" in context
                assert "ou_123" in context
                return {"messages": [AIMessage(content="Meeting booked for Wang Xing in Shanghai.")]}

            if agent_name == "contacts-agent":
                return {
                    "messages": [
                        AIMessage(content='{"openId":"ou_123","city":"Shanghai","personName":"Wang Xing"}')
                    ]
                }

            raise AssertionError(f"Unexpected agent: {agent_name}")

    async def _run():
        from src.agents.graph import build_multi_agent_graph_for_test

        planner_llm = PlannerLLMWithDependency()
        domain_stub = DomainAgentStubWithHelp()
        checkpointer = MemorySaver()
        events: list[dict] = []

        with ExitStack() as stack:
            stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
            stack.enter_context(
                patch(
                    "src.agents.router.semantic_router.list_domain_agents",
                    return_value=[
                        type("Agent", (), {"name": "meeting-agent", "description": "Book meetings"})(),
                        type("Agent", (), {"name": "contacts-agent", "description": "Lookup contacts"})(),
                    ],
                )
            )
            stack.enter_context(patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None))
            stack.enter_context(patch("src.agents.executor.executor.make_lead_agent", create=True, new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.lead_agent.agent.make_lead_agent", new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.executor.executor.get_stream_writer", return_value=events.append))
            stack.enter_context(patch("src.agents.router.semantic_router.get_stream_writer", return_value=events.append))

            graph = build_multi_agent_graph_for_test(checkpointer=checkpointer)
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

            final_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="Book a meeting room for Wang Xing tomorrow morning.")]},
                config=config,
            )

            assert final_state.get("execution_state") == "DONE"
            assert final_state.get("final_result") == "Meeting booked for Wang Xing in Shanghai."
            assert final_state.get("workflow_stage") == "summarizing"
            assert final_state.get("workflow_stage_detail") == "Meeting booked for Wang Xing in Shanghai."
            assert len(final_state.get("verified_facts", {})) == 2
            parent_task = next(task for task in final_state["task_pool"] if task["assigned_agent"] == "meeting-agent")
            helper_task = next(task for task in final_state["task_pool"] if task["assigned_agent"] == "contacts-agent")
            assert parent_task["status"] == "DONE"
            assert helper_task["status"] == "DONE"
            assert parent_task["resolved_inputs"][helper_task["task_id"]]["openId"] == "ou_123"
            assert {event["type"] for event in events} >= {
                "task_waiting_dependency",
                "task_help_requested",
                "task_resumed",
                "task_completed",
            }

    asyncio.run(_run())


def test_multi_agent_graph_intervention_fast_path_does_not_retrigger_old_room_request():
    class PlannerLLMWithIntervention:
        def __init__(self):
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            if self.call_count == 1:
                return DummyResponse('[{"description": "book a meeting room", "assigned_agent": "meeting-agent"}]')
            return DummyResponse('{"done": true, "summary": "Meeting booked successfully."}')

    class DomainAgentStubWithIntervention:
        def __init__(self):
            self.calls: dict[str, int] = {}

        async def ainvoke(self, payload, config=None):
            agent_name = config.get("configurable", {}).get("agent_name")
            self.calls[agent_name] = self.calls.get(agent_name, 0) + 1

            if agent_name != "meeting-agent":
                raise AssertionError(f"Unexpected agent: {agent_name}")

            if self.calls[agent_name] == 1:
                return {
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "problem": "Need the user to select a room",
                                    "required_capability": "room selection",
                                    "reason": "Several rooms are available",
                                    "expected_output": "selected room id",
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

            if self.calls[agent_name] == 2:
                context = next(
                    (m.content for m in reversed(payload["messages"]) if isinstance(m, HumanMessage)),
                    payload["messages"][0].content,
                )
                assert "User clarification answer:\nRoom A" in context
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "create-1",
                                    "name": "meeting_createMeeting",
                                    "args": {"roomId": "room-a", "topic": "Weekly sync"},
                                }
                            ],
                        ),
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "request_id": "intv-1",
                                    "fingerprint": "fp-1",
                                    "context": {"idempotency_key": "idem-1"},
                                    "action_schema": {
                                        "actions": [
                                            {
                                                "key": "approve",
                                                "label": "Approve",
                                                "kind": "button",
                                                "resolution_behavior": "resume_current_task",
                                            },
                                            {
                                                "key": "reject",
                                                "label": "Reject",
                                                "kind": "button",
                                                "resolution_behavior": "fail_current_task",
                                            },
                                        ]
                                    },
                                }
                            ),
                            tool_call_id="create-1",
                            name="intervention_required",
                        ),
                    ]
                }

            tool_names = [getattr(m, "name", None) for m in payload["messages"]]
            assert "meeting_createMeeting" in tool_names
            assert "request_help" not in tool_names
            return {
                "messages": [
                    ToolMessage(
                        content='{"result_text":"Meeting booked successfully.","fact_payload":{"status":"booked"}}',
                        tool_call_id="done-1",
                        name="task_complete",
                    )
                ]
            }

    async def _run():
        from src.agents.graph import build_multi_agent_graph_for_test

        planner_llm = PlannerLLMWithIntervention()
        domain_stub = DomainAgentStubWithIntervention()
        checkpointer = MemorySaver()
        events: list[dict] = []

        async def _execute_intercepted_tool_call(stored_tool_call, _config):
            return ToolMessage(
                content='{"meetingId":"mtg-1","status":"booked"}',
                tool_call_id=stored_tool_call["tool_call_id"],
                name=stored_tool_call["tool_name"],
            )

        with ExitStack() as stack:
            stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
            stack.enter_context(
                patch(
                    "src.agents.router.semantic_router.list_domain_agents",
                    return_value=[type("Agent", (), {"name": "meeting-agent", "description": "Book meetings"})()],
                )
            )
            stack.enter_context(patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None))
            stack.enter_context(
                patch("src.agents.executor.executor._execute_intercepted_tool_call", new=_execute_intercepted_tool_call)
            )
            stack.enter_context(patch("src.agents.executor.executor.make_lead_agent", create=True, new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.lead_agent.agent.make_lead_agent", new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.executor.executor.get_stream_writer", return_value=events.append))
            stack.enter_context(patch("src.agents.router.semantic_router.get_stream_writer", return_value=events.append))

            graph = build_multi_agent_graph_for_test(checkpointer=checkpointer)
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

            first_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="Book a meeting room for next week.")]},
                config=config,
            )
            first_task = first_state["task_pool"][0]
            assert first_state["execution_state"] == "INTERRUPTED"
            assert first_task["status"] == "RUNNING"
            assert first_task["clarification_prompt"] == "Which room should I book?\n\n1. Room A\n2. Room B"

            second_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="Room A")]},
                config=config,
            )
            second_task = second_state["task_pool"][0]
            assert second_state["execution_state"] == "INTERRUPTED"
            assert second_task["status"] == "WAITING_INTERVENTION"
            assert second_task["continuation_mode"] == "resume_tool_call"
            assert second_task["pending_tool_call"]["tool_name"] == "meeting_createMeeting"

            third_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="[intervention_resolved] request_id=intv-1 action_key=approve")]},
                config=config,
            )

            assert third_state["execution_state"] == "DONE"
            assert third_state["final_result"] == "Meeting booked successfully."
            assert third_state["task_pool"][0]["status"] == "DONE"
            event_types = [event["type"] for event in events]
            assert event_types.count("task_waiting_intervention") == 1
            assert event_types.count("task_waiting_dependency") == 1
            assert event_types.count("task_completed") == 1

    asyncio.run(_run())


def test_multi_agent_graph_terminates_on_system_fallback():
    class UnsupportedPlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse(
                '[{"description": "Tell the user this request is unsupported", "assigned_agent": "SYSTEM_FALLBACK"}]'
            )

    async def _run():
        from src.agents.graph import build_multi_agent_graph_for_test

        planner_llm = UnsupportedPlannerLLM()
        checkpointer = MemorySaver()
        events: list[dict] = []

        with ExitStack() as stack:
            stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
            stack.enter_context(patch("src.agents.router.semantic_router.list_domain_agents", return_value=[]))
            stack.enter_context(patch("src.agents.executor.executor.get_stream_writer", return_value=events.append))

            graph = build_multi_agent_graph_for_test(checkpointer=checkpointer)
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

            final_state = await graph.ainvoke(
                {"messages": [HumanMessage(content="Book a meeting room for tomorrow morning.")]},
                config=config,
            )

            assert final_state.get("execution_state") == "DONE"
            assert final_state.get("final_result") == SYSTEM_FALLBACK_FINAL_MESSAGE
            assert final_state.get("messages")[-1].content == SYSTEM_FALLBACK_FINAL_MESSAGE
            assert final_state.get("workflow_stage") == "summarizing"
            assert final_state.get("workflow_stage_detail") == SYSTEM_FALLBACK_FINAL_MESSAGE
            assert len(final_state.get("task_pool", [])) == 1
            assert final_state["task_pool"][0]["assigned_agent"] == "SYSTEM_FALLBACK"
            assert final_state["task_pool"][0]["status"] == "DONE"
            assert len([event for event in events if event["type"] == "task_completed"]) == 1
            assert not [event for event in events if event["type"] == "task_failed"]

    asyncio.run(_run())
