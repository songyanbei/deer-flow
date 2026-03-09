from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.executor.executor import _ensure_mcp_ready, _mcp_initialized, executor_node
from src.agents.planner.node import planner_node
from src.agents.router.semantic_router import router_node


class DummyResponse:
    def __init__(self, content):
        self.content = content


def test_planner_invalid_json_returns_error():
    class InvalidPlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse("not-json")

    async def _run():
        with patch("src.agents.planner.node.create_chat_model", return_value=InvalidPlannerLLM()):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[]):
                result = await planner_node(
                    {"messages": [HumanMessage(content="Find Wang Mingtian's employee id")]},
                    {"configurable": {}},
                )
        assert result["execution_state"] == "ERROR"
        assert result["final_result"] == "Planner failed to produce valid structured output."

    asyncio.run(_run())


def test_planner_empty_unfinished_tasks_returns_error():
    class EmptyTaskPlannerLLM:
        def __init__(self):
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            if self.call_count == 1:
                return DummyResponse('[{"description": "check directory", "assigned_agent": "contacts-agent"}]')
            return DummyResponse('{"done": false, "tasks": []}')

    async def _run():
        llm = EmptyTaskPlannerLLM()
        with patch("src.agents.planner.node.create_chat_model", return_value=llm):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
                first = await planner_node(
                    {"messages": [HumanMessage(content="Find Wang Mingtian's employee id")]},
                    {"configurable": {}},
                )
                second = await planner_node(
                    {
                        "messages": [HumanMessage(content="Find Wang Mingtian's employee id")],
                        "task_pool": [{**first["task_pool"][0], "status": "DONE", "result": "No match"}],
                        "planner_goal": "Find Wang Mingtian's employee id",
                        "original_input": "Find Wang Mingtian's employee id",
                    },
                    {"configurable": {}},
                )
        assert second["execution_state"] == "ERROR"
        assert second["final_result"] == "Planner produced no actionable tasks for an unfinished goal."

    asyncio.run(_run())


def test_planner_run_id_changes_on_new_user_turn_and_reuses_on_clarification():
    class PlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse('[{"description": "check directory", "assigned_agent": "contacts-agent"}]')

    async def _run():
        with patch("src.agents.planner.node.create_chat_model", return_value=PlannerLLM()):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
                first = await planner_node(
                    {"messages": [HumanMessage(content="Find Wang Mingtian's employee id")]},
                    {"configurable": {}},
                )

                reset = await planner_node(
                    {
                        "messages": [
                            HumanMessage(content="Find Wang Mingtian's employee id"),
                            AIMessage(content="Employee ID is A-1001"),
                            HumanMessage(content="Now check Alice's employee id"),
                        ],
                        "task_pool": [],
                        "run_id": first["run_id"],
                        "original_input": "Find Wang Mingtian's employee id",
                        "planner_goal": "Find Wang Mingtian's employee id",
                    },
                    {"configurable": {}},
                )

                clarified = await planner_node(
                    {
                        "messages": [
                            HumanMessage(content="Find Wang Mingtian's employee id"),
                            AIMessage(content="Please confirm the employee identity.", name="ask_clarification"),
                            HumanMessage(content="Wang Mingtian from R&D"),
                        ],
                        "task_pool": [first["task_pool"][0]],
                        "run_id": first["run_id"],
                        "original_input": "Find Wang Mingtian's employee id",
                        "planner_goal": "Find Wang Mingtian's employee id",
                    },
                    {"configurable": {}},
                )

        assert first["run_id"].startswith("run_")
        assert all(task["run_id"] == first["run_id"] for task in first["task_pool"])
        assert reset["run_id"] != first["run_id"]
        assert reset["execution_state"] == "PLANNING_RESET"
        assert clarified["execution_state"] == "RESUMING"
        assert "run_id" not in clarified or clarified["run_id"] == first["run_id"]

    asyncio.run(_run())


def test_planner_backfills_run_id_for_legacy_resumed_tasks():
    async def _run():
        result = await planner_node(
            {
                "messages": [HumanMessage(content="Find Wang Mingtian's employee id")],
                "task_pool": [
                    {
                        "task_id": "t1",
                        "description": "lookup employee id",
                        "assigned_agent": "contacts-agent",
                        "status": "RUNNING",
                    }
                ],
                "original_input": "Find Wang Mingtian's employee id",
                "planner_goal": "Find Wang Mingtian's employee id",
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "RESUMING"
        assert result["run_id"].startswith("run_")
        assert result["task_pool"][0]["run_id"] == result["run_id"]
        assert result["task_pool"][0]["updated_at"]

    asyncio.run(_run())


def test_executor_empty_output_marks_task_failed(monkeypatch):
    class EmptyDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {"messages": [AIMessage(content="   ")]}

    def _make_lead_agent(_config):
        return EmptyDomainAgent()

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[]))
    monkeypatch.setattr("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent)
    _mcp_initialized.clear()

    async def _run():
        with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
            result = await executor_node(
                {
                    "task_pool": [
                        {
                            "task_id": "t1",
                            "description": "lookup employee id",
                            "assigned_agent": "contacts-agent",
                            "status": "RUNNING",
                        }
                    ],
                    "verified_facts": {},
                },
                {"configurable": {}},
            )

        assert result["execution_state"] == "EXECUTING_DONE"
        assert result["task_pool"][0]["status"] == "FAILED"
        assert result["task_pool"][0]["error"] == "Domain agent returned no final answer."

    asyncio.run(_run())


def test_executor_waiting_clarification_persists_task_and_emits_protocol_fields(monkeypatch):
    class ClarifyingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content="Please confirm the employee identity.",
                        tool_call_id="clarify-1",
                        name="ask_clarification",
                    )
                ]
            }

    def _make_lead_agent(_config):
        return ClarifyingDomainAgent()

    events: list[dict] = []

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[]))
    monkeypatch.setattr("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent)
    _mcp_initialized.clear()

    async def _run():
        with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
            with patch("src.agents.executor.executor.get_stream_writer", return_value=events.append):
                result = await executor_node(
                    {
                        "run_id": "run_test123",
                        "task_pool": [
                            {
                                "task_id": "t1",
                                "description": "lookup employee id",
                                "run_id": "run_test123",
                                "assigned_agent": "contacts-agent",
                                "status": "RUNNING",
                            }
                        ],
                        "verified_facts": {},
                    },
                    {"configurable": {}},
                )

        assert result["execution_state"] == "INTERRUPTED"
        assert result["task_pool"][0]["status"] == "RUNNING"
        assert result["task_pool"][0]["clarification_prompt"] == "Please confirm the employee identity."
        assert result["task_pool"][0]["status_detail"] == "Waiting for user clarification"
        assert [event["type"] for event in events] == ["task_started", "task_running", "task_running"]
        assert all(event["source"] == "multi_agent" for event in events)
        assert all(event["run_id"] == "run_test123" for event in events)
        assert events[-1]["status"] == "waiting_clarification"
        assert events[-1]["clarification_prompt"] == "Please confirm the employee identity."

    asyncio.run(_run())


def test_router_preserves_run_id_and_adds_status_metadata():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "t1",
                        "description": "lookup employee id",
                        "run_id": "run_test123",
                        "assigned_agent": "contacts-agent",
                        "status": "PENDING",
                    }
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "ROUTING_DONE"
        assert result["task_pool"][0]["status"] == "RUNNING"
        assert result["task_pool"][0]["run_id"] == "run_test123"
        assert result["task_pool"][0]["status_detail"] == "Assigned to contacts-agent"
        assert result["task_pool"][0]["updated_at"]

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
        asyncio.run(_run())


def test_executor_generates_run_id_when_called_with_legacy_running_task(monkeypatch):
    class EmptyDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {"messages": [AIMessage(content="Employee ID is A-1001")]}

    def _make_lead_agent(_config):
        return EmptyDomainAgent()

    events: list[dict] = []

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[]))
    monkeypatch.setattr("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent)
    _mcp_initialized.clear()

    async def _run():
        with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
            with patch("src.agents.executor.executor.get_stream_writer", return_value=events.append):
                result = await executor_node(
                    {
                        "task_pool": [
                            {
                                "task_id": "t1",
                                "description": "lookup employee id",
                                "assigned_agent": "contacts-agent",
                                "status": "RUNNING",
                            }
                        ],
                        "verified_facts": {},
                    },
                    {"configurable": {}},
                )

        assert result["task_pool"][0]["run_id"].startswith("run_")
        assert all(event["run_id"] == result["task_pool"][0]["run_id"] for event in events)

    asyncio.run(_run())


def test_ensure_mcp_ready_retries_after_failure(monkeypatch):
    class DummyServer:
        def model_dump(self):
            return {"name": "dummy", "command": "node"}

    class DummyPool:
        def __init__(self):
            self.calls = 0

        async def init_agent_connections(self, _agent_name, _servers):
            self.calls += 1
            return self.calls > 1

        def get_agent_error(self, _agent_name):
            return "initial connect failed"

    dummy_pool = DummyPool()

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[DummyServer()]))
    monkeypatch.setattr("src.execution.mcp_pool.mcp_pool", dummy_pool)
    _mcp_initialized.clear()

    async def _run():
        try:
            await _ensure_mcp_ready("contacts-agent")
        except RuntimeError as exc:
            assert str(exc) == "initial connect failed"
        else:
            raise AssertionError("Expected the first MCP init to fail.")

        assert "contacts-agent" not in _mcp_initialized

        await _ensure_mcp_ready("contacts-agent")
        assert "contacts-agent" in _mcp_initialized
        assert dummy_pool.calls == 2

    asyncio.run(_run())
