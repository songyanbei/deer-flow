from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.executor.executor import _ensure_mcp_ready, _mcp_initialized, executor_node
from src.agents.graph import route_after_workflow_executor
from src.agents.planner.node import planner_node
from src.agents.router.semantic_router import router_node
from src.tools.tools import get_available_tools


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


def test_planner_model_invocation_error_returns_visible_error_message():
    class FailingPlannerLLM:
        async def ainvoke(self, _messages):
            raise RuntimeError("Connection error.")

    async def _run():
        with patch("src.agents.planner.node.create_chat_model", return_value=FailingPlannerLLM()):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[]):
                result = await planner_node(
                    {"messages": [HumanMessage(content="Find Wang Mingtian's employee id")]},
                    {"configurable": {}},
                )
        assert result["execution_state"] == "ERROR"
        assert result["final_result"] == "Workflow planning failed: Connection error."
        assert result["messages"][0].content == "Workflow planning failed: Connection error."

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


def test_executor_prefers_final_ai_message_over_trailing_tool_output(monkeypatch):
    class DomainAgentWithTrailingTool:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    AIMessage(content="The launch brief is complete."),
                    ToolMessage(
                        content="artifact generated",
                        tool_call_id="present-1",
                        name="present_files",
                    ),
                ]
            }

    def _make_lead_agent(_config):
        return DomainAgentWithTrailingTool()

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
                            "description": "prepare launch brief",
                            "assigned_agent": "copy-agent",
                            "status": "RUNNING",
                        }
                    ],
                    "verified_facts": {},
                },
                {"configurable": {}},
            )

        assert result["task_pool"][0]["status"] == "DONE"
        assert result["task_pool"][0]["result"] == "The launch brief is complete."
        assert result["verified_facts"]["t1"]["summary"] == "The launch brief is complete."

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


def test_executor_request_help_moves_task_to_waiting_dependency(monkeypatch):
    class HelpingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content='{"problem":"Missing organizer openId","required_capability":"contact lookup","reason":"Meeting API requires organizer identity","expected_output":"Organizer openId and city","candidate_agents":["contacts-agent"]}',
                        tool_call_id="help-1",
                        name="request_help",
                    )
                ]
            }

    def _make_lead_agent(_config):
        return HelpingDomainAgent()

    events: list[dict] = []

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[]))
    monkeypatch.setattr("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent)
    _mcp_initialized.clear()

    async def _run():
        with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
            with patch("src.agents.executor.executor.get_stream_writer", return_value=events.append):
                result = await executor_node(
                    {
                        "run_id": "run_help123",
                        "task_pool": [
                            {
                                "task_id": "t1",
                                "description": "book a meeting room",
                                "run_id": "run_help123",
                                "assigned_agent": "meeting-agent",
                                "status": "RUNNING",
                            }
                        ],
                        "verified_facts": {},
                    },
                    {"configurable": {}},
                )

        assert result["execution_state"] == "EXECUTING_DONE"
        waiting_task = result["task_pool"][0]
        assert waiting_task["status"] == "WAITING_DEPENDENCY"
        assert waiting_task["requested_by_agent"] == "meeting-agent"
        assert waiting_task["blocked_reason"] == "Meeting API requires organizer identity"
        assert waiting_task["help_depth"] == 1
        assert waiting_task["request_help"]["required_capability"] == "contact lookup"
        assert [event["type"] for event in events] == [
            "task_started",
            "task_running",
            "task_waiting_dependency",
            "task_help_requested",
        ]
        assert events[-1]["request_help"]["expected_output"] == "Organizer openId and city"

    asyncio.run(_run())


def test_executor_request_help_keeps_user_clarification_metadata(monkeypatch):
    class HelpingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content='{"problem":"No room available in Jinan","required_capability":"user preference confirmation","reason":"Need the user to choose another city before booking","expected_output":"Confirmed target city for booking","resolution_strategy":"user_clarification","clarification_question":"济南当前没有可用会议室，要改订哪个城市？","clarification_options":["北京","上海","深圳"],"clarification_context":"明天 9:00-10:00 时段济南无可用会议室。"}',
                        tool_call_id="help-clarify-1",
                        name="request_help",
                    )
                ]
            }

    def _make_lead_agent(_config):
        return HelpingDomainAgent()

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
                            "description": "book a meeting room",
                            "assigned_agent": "meeting-agent",
                            "status": "RUNNING",
                        }
                    ],
                    "verified_facts": {},
                },
                {"configurable": {}},
            )

        request = result["task_pool"][0]["request_help"]
        assert request["resolution_strategy"] == "user_clarification"
        assert request["clarification_question"] == "济南当前没有可用会议室，要改订哪个城市？"
        assert request["clarification_options"] == ["北京", "上海", "深圳"]
        assert request["clarification_context"] == "明天 9:00-10:00 时段济南无可用会议室。"

    asyncio.run(_run())


def test_route_after_workflow_executor_sends_waiting_dependency_back_to_router():
    assert route_after_workflow_executor(
        {
            "execution_state": "EXECUTING_DONE",
            "task_pool": [
                {
                    "task_id": "t1",
                    "description": "book a meeting room",
                    "status": "WAITING_DEPENDENCY",
                }
            ],
        }
    ) == "router"


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


def test_router_routes_waiting_dependency_task_to_helper():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 1,
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                            "candidate_agents": ["contacts-agent"],
                        },
                    }
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "ROUTING_DONE"
        assert len(result["task_pool"]) == 2
        parent = next(task for task in result["task_pool"] if task["task_id"] == "parent-1")
        helper = next(task for task in result["task_pool"] if task["task_id"] != "parent-1")
        assert parent["status"] == "WAITING_DEPENDENCY"
        assert parent["depends_on_task_ids"] == [helper["task_id"]]
        assert helper["status"] == "RUNNING"
        assert helper["parent_task_id"] == "parent-1"
        assert helper["assigned_agent"] == "contacts-agent"
        assert helper["run_id"] == "run_help123"

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
        asyncio.run(_run())


def test_router_interrupts_for_user_clarification_help_request():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 1,
                        "request_help": {
                            "problem": "No room available in Jinan",
                            "required_capability": "user preference confirmation",
                            "reason": "Need the user to choose another city before booking",
                            "expected_output": "Confirmed target city for booking",
                            "resolution_strategy": "user_clarification",
                            "clarification_question": "济南当前没有可用会议室，要改订哪个城市？",
                            "clarification_options": ["北京", "上海", "深圳"],
                            "clarification_context": "明天 9:00-10:00 时段济南无可用会议室。",
                        },
                    }
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "INTERRUPTED"
        task = result["task_pool"][0]
        assert task["status"] == "RUNNING"
        assert task["request_help"] is None
        assert task["clarification_prompt"] == (
            "明天 9:00-10:00 时段济南无可用会议室。\n\n"
            "济南当前没有可用会议室，要改订哪个城市？\n\n"
            "1. 北京\n"
            "2. 上海\n"
            "3. 深圳"
        )
        assert result["messages"][0].name == "ask_clarification"

    asyncio.run(_run())


def test_router_falls_back_to_clarification_when_no_helper_matches():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 1,
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                            "candidate_agents": ["meeting-agent"],
                        },
                    }
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "INTERRUPTED"
        task = result["task_pool"][0]
        assert task["status"] == "RUNNING"
        assert task["clarification_prompt"]
        assert task["request_help"] is None
        assert task["blocked_reason"] is None
        assert task["depends_on_task_ids"] == []
        assert result["messages"][0].name == "ask_clarification"

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="meeting-agent", description="Book meetings")]):
        asyncio.run(_run())


def test_router_treats_candidate_agents_as_hints_not_hard_whitelist():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 1,
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                            "candidate_agents": ["unknown-agent"],
                        },
                    }
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        helper = next(task for task in result["task_pool"] if task["task_id"] != "parent-1")
        assert helper["assigned_agent"] == "contacts-agent"

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
        asyncio.run(_run())


def test_router_resumes_parent_after_helper_completion():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "depends_on_task_ids": ["helper-1"],
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                        },
                    },
                    {
                        "task_id": "helper-1",
                        "description": "lookup organizer",
                        "run_id": "run_help123",
                        "assigned_agent": "contacts-agent",
                        "status": "DONE",
                        "result": "{\"openId\":\"ou_123\"}",
                    },
                ],
                "verified_facts": {
                    "helper-1": {
                        "summary": "Organizer resolved",
                        "payload": {"openId": "ou_123", "city": "Shanghai"},
                    }
                },
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "ROUTING_DONE"
        resumed = result["task_pool"][0]
        assert resumed["task_id"] == "parent-1"
        assert resumed["status"] == "PENDING"
        assert resumed["request_help"] is None
        assert resumed["blocked_reason"] is None
        assert resumed["depends_on_task_ids"] == []
        assert resumed["resolved_inputs"]["helper-1"] == {
            "openId": "ou_123",
            "city": "Shanghai",
        }
        assert resumed["resume_count"] == 1

    asyncio.run(_run())


def test_router_asks_for_clarification_after_excessive_resumes():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 1,
                        "resume_count": 2,
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                        },
                    }
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "INTERRUPTED"
        task = result["task_pool"][0]
        assert task["status"] == "RUNNING"
        assert task["clarification_prompt"]
        assert task["request_help"] is None

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
        asyncio.run(_run())


def test_router_interrupts_when_dependency_task_failed():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "depends_on_task_ids": ["helper-1"],
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                        },
                    },
                    {
                        "task_id": "helper-1",
                        "description": "lookup organizer",
                        "run_id": "run_help123",
                        "assigned_agent": "contacts-agent",
                        "status": "FAILED",
                        "error": "Directory API unavailable",
                    },
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "INTERRUPTED"
        task = result["task_pool"][0]
        assert task["status"] == "RUNNING"
        assert task["request_help"] is None
        assert task["blocked_reason"] is None
        assert task["depends_on_task_ids"] == []
        assert "Directory API unavailable" in task["clarification_prompt"]
        assert result["messages"][0].name == "ask_clarification"

    asyncio.run(_run())


def test_router_interrupts_when_help_loop_budget_is_exhausted():
    async def _run():
        result = await router_node(
            {
                "task_pool": [
                    {
                        "task_id": "parent-1",
                        "description": "book a meeting room",
                        "run_id": "run_help123",
                        "assigned_agent": "meeting-agent",
                        "requested_by_agent": "meeting-agent",
                        "status": "WAITING_DEPENDENCY",
                        "help_depth": 2,
                        "resume_count": 1,
                        "request_help": {
                            "problem": "Missing organizer openId",
                            "required_capability": "contact lookup",
                            "reason": "Meeting API requires organizer identity",
                            "expected_output": "Organizer openId and city",
                        },
                    }
                ],
                "route_count": 8,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "INTERRUPTED"
        task = result["task_pool"][0]
        assert task["status"] == "RUNNING"
        assert task["request_help"] is None
        assert task["clarification_prompt"]
        assert result["messages"][0].name == "ask_clarification"

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


def test_domain_agent_tools_expose_request_help_instead_of_ask_clarification():
    tool_names = {tool.name for tool in get_available_tools(is_domain_agent=True)}

    assert "request_help" in tool_names
    assert "ask_clarification" not in tool_names


def test_top_level_tools_keep_ask_clarification_and_hide_request_help():
    tool_names = {tool.name for tool in get_available_tools(is_domain_agent=False)}

    assert "ask_clarification" in tool_names
    assert "request_help" not in tool_names


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
