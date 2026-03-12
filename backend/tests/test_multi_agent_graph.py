"""Pytest-collected integration test for the multi-agent graph."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver


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
        context = payload["messages"][0].content

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
            context = payload["messages"][0].content

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
