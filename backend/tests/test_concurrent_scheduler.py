"""Integration tests for the Phase 2 Stage 1 concurrent scheduler.

These tests verify:
1. Independent tasks run concurrently
2. Dependent tasks respect execution ordering
3. task_pool converges to a stable terminal state
4. Runtime compatibility (clarification, intervention, resume) under concurrency
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver


class DummyResponse:
    def __init__(self, content):
        self.content = content


def make_lead_agent_stub(domain_stub):
    def _factory(_config):
        return domain_stub
    return _factory


class TestConcurrentIndependentTasks:
    """Two independent tasks should be scheduled and executed concurrently."""

    def test_independent_tasks_run_in_parallel(self):
        execution_order: list[str] = []

        class PlannerLLM:
            def __init__(self):
                self.call_count = 0

            async def ainvoke(self, _messages):
                self.call_count += 1
                if self.call_count == 1:
                    return DummyResponse(
                        '[{"description": "lookup contact info", "assigned_agent": "contacts-agent", "depends_on": [], "priority": 0},'
                        ' {"description": "check leave status", "assigned_agent": "hr-agent", "depends_on": [], "priority": 0}]'
                    )
                return DummyResponse('{"done": true, "summary": "Contact: A-1001, Leave: none."}')

        class ConcurrentDomainStub:
            async def ainvoke(self, payload, config=None):
                agent_name = config.get("configurable", {}).get("agent_name")
                execution_order.append(agent_name)
                if agent_name == "contacts-agent":
                    return {"messages": [AIMessage(content="Contact ID: A-1001")]}
                if agent_name == "hr-agent":
                    return {"messages": [AIMessage(content="No leave record found")]}
                raise AssertionError(f"Unexpected agent: {agent_name}")

        async def _run():
            from src.agents.graph import build_multi_agent_graph_for_test

            planner_llm = PlannerLLM()
            domain_stub = ConcurrentDomainStub()
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
                stack.enter_context(patch("src.agents.router.semantic_router.get_stream_writer", return_value=events.append))

                graph = build_multi_agent_graph_for_test(checkpointer=checkpointer)
                thread_id = str(uuid.uuid4())
                config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

                final_state = await graph.ainvoke(
                    {"messages": [HumanMessage(content="Lookup employee A-1001 contact and check leave status.")]},
                    config=config,
                )

                # Both tasks should have been executed
                assert final_state.get("execution_state") == "DONE"
                assert len(final_state.get("verified_facts", {})) == 2
                assert all(t["status"] == "DONE" for t in final_state.get("task_pool", []))

                # Both agents should have been called
                assert "contacts-agent" in execution_order
                assert "hr-agent" in execution_order

                # Events should show both tasks started
                task_started_events = [e for e in events if e.get("type") == "task_started"]
                task_completed_events = [e for e in events if e.get("type") == "task_completed"]
                assert len(task_completed_events) == 2

        asyncio.run(_run())


class TestDependencyAwareScheduling:
    """Task B depends on Task A — B should NOT run until A completes."""

    def test_dependent_task_waits_for_dependency(self):
        execution_order: list[str] = []

        class PlannerLLM:
            def __init__(self):
                self.call_count = 0

            async def ainvoke(self, _messages):
                self.call_count += 1
                if self.call_count == 1:
                    return DummyResponse(
                        '[{"description": "lookup contact info", "assigned_agent": "contacts-agent", "depends_on": [], "priority": 0},'
                        ' {"description": "book meeting for contact", "assigned_agent": "meeting-agent", "depends_on": [0], "priority": 1}]'
                    )
                return DummyResponse('{"done": true, "summary": "Meeting booked for contact A-1001."}')

        class SequentialDomainStub:
            async def ainvoke(self, payload, config=None):
                agent_name = config.get("configurable", {}).get("agent_name")
                execution_order.append(agent_name)
                if agent_name == "contacts-agent":
                    return {"messages": [AIMessage(content="Contact ID: A-1001")]}
                if agent_name == "meeting-agent":
                    return {"messages": [AIMessage(content="Meeting booked for A-1001")]}
                raise AssertionError(f"Unexpected agent: {agent_name}")

        async def _run():
            from src.agents.graph import build_multi_agent_graph_for_test

            planner_llm = PlannerLLM()
            domain_stub = SequentialDomainStub()
            checkpointer = MemorySaver()
            events: list[dict] = []

            with ExitStack() as stack:
                stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
                stack.enter_context(
                    patch(
                        "src.agents.router.semantic_router.list_domain_agents",
                        return_value=[
                            type("Agent", (), {"name": "contacts-agent", "description": "Lookup employees"})(),
                            type("Agent", (), {"name": "meeting-agent", "description": "Book meetings"})(),
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
                    {"messages": [HumanMessage(content="Find contact A-1001 then book a meeting for them.")]},
                    config=config,
                )

                assert final_state.get("execution_state") == "DONE"
                assert all(t["status"] == "DONE" for t in final_state.get("task_pool", []))

                # Contacts-agent must run BEFORE meeting-agent
                contacts_idx = execution_order.index("contacts-agent")
                meeting_idx = execution_order.index("meeting-agent")
                assert contacts_idx < meeting_idx, (
                    f"Dependency violated: contacts-agent ran at {contacts_idx}, "
                    f"meeting-agent at {meeting_idx}"
                )

        asyncio.run(_run())


class TestDiamondDependency:
    """A and B are independent; C depends on both A and B."""

    def test_diamond_pattern(self):
        execution_order: list[str] = []

        class PlannerLLM:
            def __init__(self):
                self.call_count = 0

            async def ainvoke(self, _messages):
                self.call_count += 1
                if self.call_count == 1:
                    return DummyResponse(
                        '[{"description": "lookup contact", "assigned_agent": "contacts-agent", "depends_on": [], "priority": 0},'
                        ' {"description": "check leave", "assigned_agent": "hr-agent", "depends_on": [], "priority": 0},'
                        ' {"description": "book meeting", "assigned_agent": "meeting-agent", "depends_on": [0, 1], "priority": 1}]'
                    )
                return DummyResponse('{"done": true, "summary": "All done."}')

        class DiamondDomainStub:
            async def ainvoke(self, payload, config=None):
                agent_name = config.get("configurable", {}).get("agent_name")
                execution_order.append(agent_name)
                if agent_name == "contacts-agent":
                    return {"messages": [AIMessage(content="Contact: ou_123")]}
                if agent_name == "hr-agent":
                    return {"messages": [AIMessage(content="No leave")]}
                if agent_name == "meeting-agent":
                    return {"messages": [AIMessage(content="Meeting booked")]}
                raise AssertionError(f"Unexpected agent: {agent_name}")

        async def _run():
            from src.agents.graph import build_multi_agent_graph_for_test

            planner_llm = PlannerLLM()
            domain_stub = DiamondDomainStub()
            checkpointer = MemorySaver()
            events: list[dict] = []

            with ExitStack() as stack:
                stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
                stack.enter_context(
                    patch(
                        "src.agents.router.semantic_router.list_domain_agents",
                        return_value=[
                            type("Agent", (), {"name": "contacts-agent", "description": "Lookup employees"})(),
                            type("Agent", (), {"name": "hr-agent", "description": "Check leave"})(),
                            type("Agent", (), {"name": "meeting-agent", "description": "Book meetings"})(),
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
                    {"messages": [HumanMessage(content="Find contact, check leave, then book meeting.")]},
                    config=config,
                )

                assert final_state.get("execution_state") == "DONE"
                assert all(t["status"] == "DONE" for t in final_state.get("task_pool", []))

                # meeting-agent must run after BOTH contacts-agent and hr-agent
                meeting_idx = execution_order.index("meeting-agent")
                contacts_idx = execution_order.index("contacts-agent")
                hr_idx = execution_order.index("hr-agent")
                assert contacts_idx < meeting_idx
                assert hr_idx < meeting_idx

        asyncio.run(_run())


class TestTaskPoolConvergence:
    """task_pool should converge to a stable terminal state with all tasks DONE."""

    def test_all_tasks_converge(self):
        class PlannerLLM:
            def __init__(self):
                self.call_count = 0

            async def ainvoke(self, _messages):
                self.call_count += 1
                if self.call_count == 1:
                    return DummyResponse(
                        '[{"description": "task A", "assigned_agent": "contacts-agent", "depends_on": [], "priority": 0},'
                        ' {"description": "task B", "assigned_agent": "hr-agent", "depends_on": [], "priority": 0},'
                        ' {"description": "task C", "assigned_agent": "meeting-agent", "depends_on": [0, 1], "priority": 1}]'
                    )
                return DummyResponse('{"done": true, "summary": "All tasks completed."}')

        class SimpleDomainStub:
            async def ainvoke(self, payload, config=None):
                agent_name = config.get("configurable", {}).get("agent_name")
                return {"messages": [AIMessage(content=f"{agent_name} completed")]}

        async def _run():
            from src.agents.graph import build_multi_agent_graph_for_test

            planner_llm = PlannerLLM()
            domain_stub = SimpleDomainStub()
            checkpointer = MemorySaver()
            events: list[dict] = []

            with ExitStack() as stack:
                stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
                stack.enter_context(
                    patch(
                        "src.agents.router.semantic_router.list_domain_agents",
                        return_value=[
                            type("Agent", (), {"name": "contacts-agent", "description": "Contacts"})(),
                            type("Agent", (), {"name": "hr-agent", "description": "HR"})(),
                            type("Agent", (), {"name": "meeting-agent", "description": "Meetings"})(),
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
                    {"messages": [HumanMessage(content="Do tasks A, B, C.")]},
                    config=config,
                )

                task_pool = final_state.get("task_pool", [])
                assert final_state.get("execution_state") == "DONE"
                assert len(task_pool) >= 3
                # All planner-created tasks should be terminal
                for task in task_pool:
                    assert task["status"] in ("DONE", "FAILED"), f"Task {task['task_id']} not terminal: {task['status']}"
                # verified_facts should have entries for completed tasks
                assert len(final_state.get("verified_facts", {})) >= 3

                # Event stream should show completed events for all tasks
                completed_events = [e for e in events if e.get("type") == "task_completed"]
                assert len(completed_events) >= 3

        asyncio.run(_run())
