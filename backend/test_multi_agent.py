"""Deterministic end-to-end test for the multi_agent graph.

Run:
    cd backend
    python test_multi_agent.py
"""

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


async def main():
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

        print("=== First turn ===")
        print("execution_state:", first_state.get("execution_state"))
        print("messages:", [getattr(m, "content", "") for m in first_state.get("messages", [])[-2:]])
        print("task statuses:", [t["status"] for t in first_state.get("task_pool", [])])
        print("planner_goal:", first_state.get("planner_goal"))

        assert first_state.get("execution_state") == "INTERRUPTED"
        assert first_state.get("task_pool", [])[0]["status"] == "RUNNING"
        assert first_state.get("planner_goal") == "Check whether Wang Mingtian is on leave and tell me the employee ID."
        assert [event["type"] for event in events[:3]] == ["task_started", "task_running", "task_running"]
        assert events[2]["status"] == "waiting_clarification"

        final_state = await graph.ainvoke(
            {"messages": [HumanMessage(content="Wang Mingtian from R&D")]},
            config=config,
        )

        print("=== Resumed turn ===")
        print("execution_state:", final_state.get("execution_state"))
        print("final_result:", final_state.get("final_result"))
        print("verified_facts:", final_state.get("verified_facts"))
        print("task statuses:", [t["status"] for t in final_state.get("task_pool", [])])
        print("events:", [event["type"] for event in events])

        assert final_state.get("execution_state") == "DONE"
        assert final_state.get("final_result") == "Employee ID is A-1001 and there is no active leave record."
        assert final_state.get("planner_goal") == "Check whether Wang Mingtian is on leave and tell me the employee ID."
        assert len(final_state.get("verified_facts", {})) == 2
        assert all(t["status"] == "DONE" for t in final_state.get("task_pool", []))
        assert final_state.get("messages")[-1].content == "Employee ID is A-1001 and there is no active leave record."
        assert {event["type"] for event in events} >= {"task_started", "task_running", "task_completed"}
        assert len([event for event in events if event["type"] == "task_completed"]) == 2

        print("multi_agent integration test passed")


if __name__ == "__main__":
    asyncio.run(main())

