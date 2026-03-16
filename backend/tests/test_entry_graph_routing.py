from __future__ import annotations

import asyncio
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


async def _leader_node(_state, _config=None):
    return {"final_result": "leader-branch"}


async def _workflow_node(_state, _config=None):
    return {"final_result": "workflow-branch"}


def test_entry_graph_routes_to_leader_branch():
    async def _run():
        from src.agents.entry_graph import build_entry_graph

        with (
            patch("src.agents.entry_graph.ensure_domain_agent_mcp_warmup", return_value=None),
            patch("src.agents.entry_graph.make_lead_agent", return_value=_leader_node),
            patch("src.agents.entry_graph.planner_node", return_value={"final_result": "workflow-branch", "execution_state": "DONE"}),
        ):
            graph = build_entry_graph({"configurable": {"requested_orchestration_mode": "leader"}})
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="Hello")]},
                config={"configurable": {"requested_orchestration_mode": "leader"}},
            )

        assert result["final_result"] == "leader-branch"
        assert result["requested_orchestration_mode"] == "leader"
        assert result["resolved_orchestration_mode"] == "leader"

    asyncio.run(_run())


def test_entry_graph_routes_to_workflow_branch():
    async def _run():
        from src.agents.entry_graph import build_entry_graph

        with (
            patch("src.agents.entry_graph.ensure_domain_agent_mcp_warmup", return_value=None),
            patch("src.agents.entry_graph.make_lead_agent", return_value=_leader_node),
            patch("src.agents.entry_graph.planner_node", return_value={"final_result": "workflow-branch", "execution_state": "DONE"}),
        ):
            graph = build_entry_graph({"configurable": {"requested_orchestration_mode": "workflow"}})
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="Hello")]},
                config={"configurable": {"requested_orchestration_mode": "workflow"}},
            )

        assert result["final_result"] == "workflow-branch"
        assert result["requested_orchestration_mode"] == "workflow"
        assert result["resolved_orchestration_mode"] == "workflow"

    asyncio.run(_run())


def test_entry_graph_auto_routing_writes_reason():
    async def _run():
        from src.agents.entry_graph import build_entry_graph

        with (
            patch("src.agents.entry_graph.ensure_domain_agent_mcp_warmup", return_value=None),
            patch("src.agents.entry_graph.make_lead_agent", return_value=_leader_node),
            patch("src.agents.entry_graph.planner_node", return_value={"final_result": "workflow-branch", "execution_state": "DONE"}),
        ):
            graph = build_entry_graph({"configurable": {"requested_orchestration_mode": "auto"}})
            result = await graph.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content="Research the market, compare vendors, and summarize the results in a report.",
                        )
                    ]
                },
                config={"configurable": {"requested_orchestration_mode": "auto"}},
            )

        assert result["final_result"] == "workflow-branch"
        assert result["requested_orchestration_mode"] == "auto"
        assert result["resolved_orchestration_mode"] == "workflow"
        assert result["orchestration_reason"]

    asyncio.run(_run())


def test_entry_graph_streams_workflow_task_events_incrementally():
    class DummyResponse:
        def __init__(self, content):
            self.content = content

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
                return {"messages": [AIMessage(content="Meeting booked for Wang Xing in Shanghai.")]}

            if agent_name == "contacts-agent":
                return {
                    "messages": [
                        AIMessage(content='{"openId":"ou_123","city":"Shanghai"}')
                    ]
                }

            raise AssertionError(f"Unexpected agent: {agent_name}")

    def make_lead_agent_stub(domain_stub):
        def _factory(_config):
            return domain_stub

        return _factory

    async def _run():
        from src.agents.entry_graph import build_entry_graph

        planner_llm = PlannerLLMWithDependency()
        domain_stub = DomainAgentStubWithHelp()
        custom_events: list[dict] = []

        with ExitStack() as stack:
            stack.enter_context(patch("src.agents.entry_graph.ensure_domain_agent_mcp_warmup", return_value=None))
            stack.enter_context(patch("src.agents.planner.node.create_chat_model", return_value=planner_llm))
            stack.enter_context(
                patch(
                    "src.agents.router.semantic_router.list_domain_agents",
                    return_value=[
                        SimpleNamespace(name="meeting-agent", description="Book meetings"),
                        SimpleNamespace(name="contacts-agent", description="Lookup contacts"),
                    ],
                )
            )
            stack.enter_context(patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None))
            stack.enter_context(patch("src.agents.executor.executor.make_lead_agent", create=True, new=make_lead_agent_stub(domain_stub)))
            stack.enter_context(patch("src.agents.lead_agent.agent.make_lead_agent", new=make_lead_agent_stub(domain_stub)))

            graph = build_entry_graph({"configurable": {"requested_orchestration_mode": "workflow"}})
            config = {
                "configurable": {
                    "thread_id": str(uuid.uuid4()),
                    "requested_orchestration_mode": "workflow",
                },
                "recursion_limit": 50,
            }

            async for mode, payload in graph.astream(
                {"messages": [HumanMessage(content="Book a meeting room for Wang Xing tomorrow morning.")]},
                config=config,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    custom_events.append(payload)

        event_types = [event["type"] for event in custom_events]
        assert event_types[0] == "orchestration_mode_resolved"
        stage_events = [
            event for event in custom_events if event.get("type") == "workflow_stage_changed"
        ]
        assert stage_events[0]["workflow_stage"] == "acknowledged"
        assert stage_events[0]["run_id"].startswith("run_")
        assert stage_events[1]["workflow_stage"] == "queued"
        assert stage_events[1]["run_id"] == stage_events[0]["run_id"]
        assert "task_started" in event_types
        assert "task_waiting_dependency" in event_types
        assert "task_resumed" in event_types
        assert "task_completed" in event_types

    asyncio.run(_run())
