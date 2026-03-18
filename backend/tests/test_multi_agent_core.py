from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.executor.executor import (
    SYSTEM_FALLBACK_FINAL_MESSAGE,
    _ensure_mcp_ready,
    _mcp_initialized,
    executor_node,
)
from src.agents.graph import (
    route_after_workflow_executor,
    route_after_workflow_planner,
    route_after_workflow_router,
)
from src.agents.lead_agent.prompt import apply_prompt_template
from src.agents.middlewares.help_request_middleware import HelpRequestMiddleware
from src.agents.orchestration.selector import orchestration_selector_node
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
        assert result["final_result"] == "规划器输出格式异常，暂时无法继续执行。"
        assert result["workflow_stage"] == "planning"
        assert result["workflow_stage_detail"] == "规划器输出格式异常，暂时无法继续执行。"

    asyncio.run(_run())


def test_planner_queued_state_loops_back_to_planner():
    assert route_after_workflow_planner({"execution_state": "QUEUED"}) == "planner"
    assert route_after_workflow_planner({"execution_state": "PLANNING_DONE"}) == "router"


def test_orchestration_selector_emits_acknowledged_stage_for_workflow():
    events: list[dict] = []

    with patch(
        "src.agents.orchestration.selector.get_stream_writer",
        return_value=events.append,
    ):
        result = orchestration_selector_node(
            {"messages": [HumanMessage(content="Compare two plans and summarize the tradeoffs.")]},
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

    assert result["resolved_orchestration_mode"] == "workflow"
    assert result["workflow_stage"] == "acknowledged"
    assert result["run_id"].startswith("run_")
    assert events[0]["type"] == "orchestration_mode_resolved"
    assert events[0]["run_id"] == result["run_id"]
    assert events[1]["type"] == "workflow_stage_changed"
    assert events[1]["workflow_stage"] == "acknowledged"
    assert events[1]["run_id"] == result["run_id"]


def test_orchestration_selector_reuses_run_id_for_workflow_clarification_resume():
    existing_run_id = "run_existing123"
    events: list[dict] = []

    with patch(
        "src.agents.orchestration.selector.get_stream_writer",
        return_value=events.append,
    ):
        result = orchestration_selector_node(
            {
                "run_id": existing_run_id,
                "resolved_orchestration_mode": "workflow",
                "execution_state": "INTERRUPTED",
                "task_pool": [
                    {
                        "task_id": "task-1",
                        "description": "Book the meeting room",
                        "status": "RUNNING",
                        "clarification_prompt": "Please confirm the attendee identity.",
                    }
                ],
                "messages": [
                    HumanMessage(content="Book the meeting room"),
                    AIMessage(content="Please confirm the attendee identity.", name="ask_clarification"),
                    HumanMessage(content="Wang Xing from Shanghai"),
                ],
            },
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

    assert result["run_id"] == existing_run_id
    assert events[0]["run_id"] == existing_run_id
    assert events[1]["run_id"] == existing_run_id


def test_orchestration_selector_preserves_enqueue_time_queued_stage():
    existing_run_id = "run_existing123"
    events: list[dict] = []

    with patch(
        "src.agents.orchestration.selector.get_stream_writer",
        return_value=events.append,
    ):
        result = orchestration_selector_node(
            {
                "run_id": existing_run_id,
                "execution_state": "QUEUED",
                "workflow_stage": "queued",
                "resolved_orchestration_mode": "workflow",
                "messages": [HumanMessage(content="Compare two plans and summarize the tradeoffs.")],
            },
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

    assert result["run_id"] == existing_run_id
    assert "workflow_stage" not in result
    assert events[0]["type"] == "orchestration_mode_resolved"
    assert len(events) == 1


def test_planner_accepts_content_blocks_from_model():
    class BlockPlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse(
                [
                    {
                        "type": "text",
                        "text": '[{"description": "check directory", "assigned_agent": "contacts-agent"}]',
                    }
                ]
            )

    async def _run():
        with patch("src.agents.planner.node.create_chat_model", return_value=BlockPlannerLLM()):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
                result = await planner_node(
                    {"messages": [HumanMessage(content="Find Wang Mingtian's employee id")]},
                    {"configurable": {}},
                )
        assert result["execution_state"] == "PLANNING_DONE"
        assert result["task_pool"][0]["description"] == "check directory"

    asyncio.run(_run())


def test_planner_emits_planning_then_routing_stage_events():
    class PlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse(
                '[{"description": "book meeting room", "assigned_agent": "meeting-agent"}]'
            )

    async def _run():
        events: list[dict] = []
        with patch("src.agents.planner.node.create_chat_model", return_value=PlannerLLM()):
            with patch(
                "src.agents.planner.node.list_domain_agents",
                return_value=[SimpleNamespace(name="meeting-agent", description="Book meetings")],
            ):
                with patch(
                    "src.agents.planner.node.get_stream_writer",
                    return_value=events.append,
                ):
                    result = await planner_node(
                        {"messages": [HumanMessage(content="Book the meeting room")]},
                        {"configurable": {}},
                    )

        stage_events = [
            event for event in events if event.get("type") == "workflow_stage_changed"
        ]
        assert [event["workflow_stage"] for event in stage_events] == [
            "planning",
            "routing",
        ]
        assert result["workflow_stage"] == "routing"
        assert result["workflow_stage_detail"] == "book meeting room"

    asyncio.run(_run())


def test_planner_emits_queued_before_planning_after_workflow_acknowledged():
    class PlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse(
                '[{"description": "book meeting room", "assigned_agent": "meeting-agent"}]'
            )

    async def _run():
        queued_events: list[dict] = []
        with patch("src.agents.planner.node.get_stream_writer", return_value=queued_events.append):
            queued = await planner_node(
                {
                    "messages": [HumanMessage(content="Book the meeting room")],
                    "run_id": "run_test123",
                    "workflow_stage": "acknowledged",
                },
                {"configurable": {}},
            )

        assert queued["execution_state"] == "QUEUED"
        assert queued["workflow_stage"] == "queued"
        assert queued["run_id"] == "run_test123"
        assert queued_events[0]["workflow_stage"] == "queued"
        assert queued_events[0]["run_id"] == "run_test123"

        planning_events: list[dict] = []
        with patch("src.agents.planner.node.create_chat_model", return_value=PlannerLLM()):
            with patch(
                "src.agents.planner.node.list_domain_agents",
                return_value=[SimpleNamespace(name="meeting-agent", description="Book meetings")],
            ):
                with patch(
                    "src.agents.planner.node.get_stream_writer",
                    return_value=planning_events.append,
                ):
                    result = await planner_node(
                        {
                            "messages": [HumanMessage(content="Book the meeting room")],
                            "run_id": queued["run_id"],
                            "workflow_stage": queued["workflow_stage"],
                            "original_input": "Book the meeting room",
                            "planner_goal": "Book the meeting room",
                            "task_pool": [],
                        },
                        {"configurable": {}},
                    )

        stage_events = [
            event for event in planning_events if event.get("type") == "workflow_stage_changed"
        ]
        assert [event["workflow_stage"] for event in stage_events] == [
            "planning",
            "routing",
        ]
        assert all(event["run_id"] == queued["run_id"] for event in stage_events)
        assert result["workflow_stage"] == "routing"

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
        assert second["workflow_stage"] == "summarizing"
        assert second["workflow_stage_detail"] == "Planner produced no actionable tasks for an unfinished goal."

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
        assert result["workflow_stage"] == "planning"
        assert result["workflow_stage_detail"] == "Workflow planning failed: Connection error."

    asyncio.run(_run())


def test_planner_done_with_null_summary_falls_back_to_task_completed():
    class NullSummaryPlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse('{"done": true, "summary": null}')

    async def _run():
        with patch("src.agents.planner.node.create_chat_model", return_value=NullSummaryPlannerLLM()):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[]):
                result = await planner_node(
                    {
                        "messages": [HumanMessage(content="Book the meeting room")],
                        "task_pool": [
                            {
                                "task_id": "t1",
                                "description": "book the meeting room",
                                "status": "DONE",
                                "result": "Room booked",
                            }
                        ],
                        "planner_goal": "Book the meeting room",
                        "original_input": "Book the meeting room",
                    },
                    {"configurable": {}},
                )
        assert result["execution_state"] == "DONE"
        assert result["final_result"] == ""
        assert result["messages"][0].content == "任务已完成。"
        assert result["workflow_stage"] == "summarizing"
        assert result["workflow_stage_detail"] == "Room booked"

    asyncio.run(_run())


def test_planner_emits_summarizing_stage_before_final_validation():
    class DonePlannerLLM:
        async def ainvoke(self, _messages):
            return DummyResponse('{"done": true, "summary": "Room booked"}')

    async def _run():
        events: list[dict] = []
        with patch("src.agents.planner.node.create_chat_model", return_value=DonePlannerLLM()):
            with patch("src.agents.planner.node.list_domain_agents", return_value=[]):
                with patch(
                    "src.agents.planner.node.get_stream_writer",
                    return_value=events.append,
                ):
                    result = await planner_node(
                        {
                            "messages": [HumanMessage(content="Book the meeting room")],
                            "task_pool": [
                                {
                                    "task_id": "t1",
                                    "description": "book the meeting room",
                                    "status": "DONE",
                                    "result": "Room booked",
                                }
                            ],
                            "planner_goal": "Book the meeting room",
                            "original_input": "Book the meeting room",
                        },
                        {"configurable": {}},
                    )

        stage_events = [
            event for event in events if event.get("type") == "workflow_stage_changed"
        ]
        assert stage_events[0]["workflow_stage"] == "summarizing"
        assert stage_events[0]["workflow_stage_detail"] == "Room booked"
        assert stage_events[-1]["workflow_stage"] == "summarizing"
        assert stage_events[-1]["workflow_stage_detail"] == "Room booked"
        assert result["workflow_stage"] == "summarizing"
        assert result["workflow_stage_detail"] == "Room booked"

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
                        "run_id": first["run_id"],
                        "original_input": "Find Wang Mingtian's employee id",
                        "planner_goal": "Find Wang Mingtian's employee id",
                        "execution_state": "INTERRUPTED",
                        "task_pool": [
                            {
                                **first["task_pool"][0],
                                "status": "RUNNING",
                                "clarification_prompt": "Please confirm the employee identity.",
                            }
                        ],
                    },
                    {"configurable": {}},
                )

        assert first["run_id"].startswith("run_")
        assert all(task["run_id"] == first["run_id"] for task in first["task_pool"])
        assert reset["run_id"] != first["run_id"]
        assert reset["execution_state"] == "QUEUED"
        assert reset["workflow_stage"] == "queued"
        assert clarified["execution_state"] == "RESUMING"
        assert "run_id" not in clarified or clarified["run_id"] == first["run_id"]

    asyncio.run(_run())


def test_planner_starts_new_run_when_user_redirects_after_clarification():
    async def _run():
        result = await planner_node(
            {
                "messages": [
                    HumanMessage(content="Book the meeting room"),
                    AIMessage(
                        content="Which building should I book?",
                        name="ask_clarification",
                    ),
                    HumanMessage(
                        content="Actually ignore that and draft a quarterly hiring plan instead.",
                    ),
                ],
                "task_pool": [
                    {
                        "task_id": "task-1",
                        "description": "Book the meeting room",
                        "status": "RUNNING",
                        "run_id": "run_existing",
                        "clarification_prompt": "Which building should I book?",
                        "updated_at": "2026-03-13T10:00:00.000Z",
                    }
                ],
                "run_id": "run_existing",
                "original_input": "Book the meeting room",
                "planner_goal": "Book the meeting room",
                "resolved_orchestration_mode": "workflow",
                "execution_state": "INTERRUPTED",
            },
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

        assert result["execution_state"] == "QUEUED"
        assert result["workflow_stage"] == "queued"
        assert result["task_pool"] == []
        assert result["run_id"] != "run_existing"
        assert result["original_input"] == (
            "Actually ignore that and draft a quarterly hiring plan instead."
        )

    asyncio.run(_run())


def test_planner_resumes_when_pending_clarification_exists_even_without_tool_copy():
    async def _run():
        result = await planner_node(
            {
                "messages": [
                    HumanMessage(content="Prepare the report"),
                    AIMessage(
                        content="Need clarification: which region should I focus on?",
                        name="assistant",
                    ),
                    HumanMessage(content="Japan only."),
                ],
                "task_pool": [
                    {
                        "task_id": "task-1",
                        "description": "Prepare the report",
                        "status": "RUNNING",
                        "run_id": "run_existing",
                        "clarification_prompt": "Which region should I focus on?",
                        "updated_at": "2026-03-13T10:00:00.000Z",
                    }
                ],
                "run_id": "run_existing",
                "original_input": "Prepare the report",
                "planner_goal": "Prepare the report",
                "resolved_orchestration_mode": "workflow",
                "execution_state": "INTERRUPTED",
            },
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

        assert result["execution_state"] == "RESUMING"
        assert result["workflow_stage"] == "executing"
        assert result["run_id"] == "run_existing"
        assert "task_pool" not in result

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


def test_router_sets_executing_stage_when_assigning_pending_task():
    async def _run():
        events: list[dict] = []
        with patch(
            "src.agents.router.semantic_router.list_domain_agents",
            return_value=[SimpleNamespace(name="meeting-agent", description="Book meetings")],
        ):
            with patch(
                "src.agents.router.semantic_router.get_stream_writer",
                return_value=events.append,
            ):
                result = await router_node(
                    {
                        "task_pool": [
                            {
                                "task_id": "t1",
                                "description": "book the meeting room",
                                "assigned_agent": "meeting-agent",
                                "status": "PENDING",
                            }
                        ]
                    },
                    {"configurable": {}},
                )

        stage_events = [
            event for event in events if event.get("type") == "workflow_stage_changed"
        ]
        assert result["workflow_stage"] == "executing"
        assert "meeting-agent" in (result["workflow_stage_detail"] or "")
        assert stage_events[0]["workflow_stage"] == "executing"

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
        assert result["task_pool"][0]["status_detail"] == "@waiting_clarification"
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
                        content=(
                            '{"problem":"Missing organizer openId","required_capability":"contact lookup",'
                            '"reason":"Meeting API requires organizer identity","expected_output":"Organizer openId and city",'
                            '"resolution_strategy":null,"clarification_question":null,"clarification_context":null,'
                            '"candidate_agents":["contacts-agent"]}'
                        ),
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
        assert "resolution_strategy" not in waiting_task["request_help"]
        assert "clarification_question" not in waiting_task["request_help"]
        assert "clarification_context" not in waiting_task["request_help"]
        assert [event["type"] for event in events] == [
            "task_started",
            "task_running",
            "task_waiting_dependency",
            "task_help_requested",
        ]
        assert events[-1]["request_help"]["expected_output"] == "Organizer openId and city"

    asyncio.run(_run())


def test_executor_request_help_honors_non_terminal_tool_signal(monkeypatch):
    class HelpingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content='{"problem":"Missing organizer openId","required_capability":"contact lookup","reason":"Meeting API requires organizer identity","expected_output":"Organizer openId","candidate_agents":["contacts-agent"]}',
                        tool_call_id="help-1",
                        name="request_help",
                    ),
                    AIMessage(content="I already asked contacts-agent to look up the organizer."),
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

        task = result["task_pool"][0]
        assert result["execution_state"] == "EXECUTING_DONE"
        assert task["status"] == "WAITING_DEPENDENCY"
        assert task["request_help"]["required_capability"] == "contact lookup"
        assert task.get("result") is None

    asyncio.run(_run())


def test_executor_request_help_keeps_user_clarification_metadata(monkeypatch):
    class HelpingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content=(
                            '{"problem":"No room available in Jinan","required_capability":"user preference confirmation",'
                            '"reason":"Need the user to choose another city before booking",'
                            '"expected_output":"Confirmed target city for booking",'
                            '"resolution_strategy":"user_clarification",'
                            '"clarification_question":"济南当前没有可用会议室，要改订哪个城市？",'
                            '"clarification_options":["北京","上海","深圳"],'
                            '"clarification_context":"明天 9:00-10:00 时段济南无可用会议室。"}'
                        ),
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


def test_executor_clarification_honors_non_terminal_tool_signal(monkeypatch):
    class ClarifyingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content="Please choose one of the available meeting rooms.",
                        tool_call_id="clarify-1",
                        name="ask_clarification",
                    ),
                    AIMessage(content="Waiting for the user's room selection."),
                ]
            }

    def _make_lead_agent(_config):
        return ClarifyingDomainAgent()

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

        task = result["task_pool"][0]
        assert result["execution_state"] == "INTERRUPTED"
        assert task["status"] == "RUNNING"
        assert task["clarification_prompt"] == "Please choose one of the available meeting rooms."
        assert task.get("result") is None

    asyncio.run(_run())


def test_executor_interrupts_on_plain_text_city_selection(monkeypatch):
    class ClarifyingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    AIMessage(
                        content="请选择一个城市的会议室进行预定：深圳创新会议室、广州天河会议室或北京思惠会议室，或者扩大搜索范围查看其他城市选项"
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
                        "run_id": "run_city123",
                        "task_pool": [
                            {
                                "task_id": "t1",
                                "description": "book a meeting room",
                                "run_id": "run_city123",
                                "assigned_agent": "meeting-agent",
                                "status": "RUNNING",
                            }
                        ],
                        "verified_facts": {},
                    },
                    {"configurable": {}},
                )

        task = result["task_pool"][0]
        assert result["execution_state"] == "INTERRUPTED"
        assert task["status"] == "RUNNING"
        assert task["clarification_prompt"].startswith("请选择一个城市的会议室进行预定")
        assert task["status_detail"] == "@waiting_clarification"
        assert "verified_facts" not in result
        assert events[-1]["status"] == "waiting_clarification"
        assert events[-1]["status_detail"] == "@waiting_clarification"

    asyncio.run(_run())


def test_executor_plain_text_completion_still_finishes(monkeypatch):
    class CompletingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {"messages": [AIMessage(content="会议室已预定成功，时间为明天 9:00-10:00。")]}

    def _make_lead_agent(_config):
        return CompletingDomainAgent()

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

        task = result["task_pool"][0]
        assert result["execution_state"] == "EXECUTING_DONE"
        assert task["status"] == "DONE"
        assert task["result"] == "会议室已预定成功，时间为明天 9:00-10:00。"

    asyncio.run(_run())


def test_executor_json_result_is_not_misclassified_as_clarification(monkeypatch):
    class JsonDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {"messages": [AIMessage(content='{"roomId":"r_123","status":"booked"}')]}

    def _make_lead_agent(_config):
        return JsonDomainAgent()

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

        task = result["task_pool"][0]
        assert result["execution_state"] == "EXECUTING_DONE"
        assert task["status"] == "DONE"
        assert task["result"] == '{"roomId":"r_123","status":"booked"}'
        assert result["verified_facts"]["t1"]["payload"] == {"roomId": "r_123", "status": "booked"}

    asyncio.run(_run())


def test_help_request_middleware_preserves_user_clarification_metadata():
    middleware = HelpRequestMiddleware()

    serialized = middleware._serialize_payload(
        {
            "problem": "No room available in Jinan",
            "required_capability": "user preference confirmation",
            "reason": "Need the user to choose another city before booking",
            "expected_output": "Confirmed target city for booking",
            "resolution_strategy": "user_clarification",
            "clarification_question": "济南当前没有可用会议室，要改订哪个城市？",
            "clarification_options": ["北京", "上海", "深圳"],
            "clarification_context": "明天 9:00-10:00 时段济南无可用会议室。",
            "candidate_agents": ["meeting-agent"],
        }
    )

    assert '"resolution_strategy": "user_clarification"' in serialized
    assert '"clarification_question": "济南当前没有可用会议室，要改订哪个城市？"' in serialized
    assert '"clarification_options": ["北京", "上海", "深圳"]' in serialized
    assert '"clarification_context": "明天 9:00-10:00 时段济南无可用会议室。"' in serialized


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
        assert result["task_pool"][0]["status_detail"] == "@assigned:contacts-agent"
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


def test_router_skips_llm_when_single_hinted_candidate_matches():
    """When candidate_agents hint resolves to exactly one valid candidate, skip LLM routing."""

    async def _run():
        with patch(
            "src.agents.router.semantic_router._llm_route",
            side_effect=AssertionError("_llm_route should not be called when exactly one hinted candidate matches"),
        ):
            result = await router_node(
                {
                    "task_pool": [
                        {
                            "task_id": "parent-1",
                            "description": "book a meeting room",
                            "run_id": "run_hint",
                            "assigned_agent": "meeting-agent",
                            "requested_by_agent": "meeting-agent",
                            "status": "WAITING_DEPENDENCY",
                            "help_depth": 1,
                            "request_help": {
                                "problem": "Missing organizer openId",
                                "required_capability": "contact lookup",
                                "reason": "Meeting API requires organizer identity",
                                "expected_output": "Organizer openId",
                                "candidate_agents": ["contacts-agent"],
                            },
                        }
                    ],
                    "route_count": 0,
                },
                {"configurable": {}},
            )

        helper = next(task for task in result["task_pool"] if task["task_id"] != "parent-1")
        assert helper["assigned_agent"] == "contacts-agent"

    with patch(
        "src.agents.router.semantic_router.list_domain_agents",
        return_value=[
            SimpleNamespace(name="contacts-agent", description="Lookup employees"),
            SimpleNamespace(name="hr-agent", description="HR operations"),
        ],
    ):
        asyncio.run(_run())


def test_router_uses_llm_when_multiple_hinted_candidates_match():
    async def _run():
        with patch(
            "src.agents.router.semantic_router._llm_route",
            return_value="contacts-agent",
        ) as llm_route:
            result = await router_node(
                {
                    "task_pool": [
                        {
                            "task_id": "parent-1",
                            "description": "book a meeting room",
                            "run_id": "run_hint",
                            "assigned_agent": "meeting-agent",
                            "requested_by_agent": "meeting-agent",
                            "status": "WAITING_DEPENDENCY",
                            "help_depth": 1,
                            "request_help": {
                                "problem": "Missing organizer openId",
                                "required_capability": "contact lookup",
                                "reason": "Meeting API requires organizer identity",
                                "expected_output": "Organizer openId",
                                "candidate_agents": ["contacts-agent", "hr-agent"],
                            },
                        }
                    ],
                    "route_count": 0,
                },
                {"configurable": {}},
            )

        llm_route.assert_called_once()
        helper = next(task for task in result["task_pool"] if task["task_id"] != "parent-1")
        assert helper["assigned_agent"] == "contacts-agent"

    with patch(
        "src.agents.router.semantic_router.list_domain_agents",
        return_value=[
            SimpleNamespace(name="contacts-agent", description="Lookup employees"),
            SimpleNamespace(name="hr-agent", description="HR operations"),
            SimpleNamespace(name="finance-agent", description="Finance operations"),
        ],
    ):
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
        assert resumed["status"] == "RUNNING"
        assert resumed["request_help"] is None
        assert resumed["blocked_reason"] is None
        assert resumed["depends_on_task_ids"] == []
        assert resumed["resolved_inputs"]["helper-1"] == {
            "openId": "ou_123",
            "city": "Shanghai",
        }
        assert resumed["resume_count"] == 1

    asyncio.run(_run())


def test_resumed_parent_interrupts_when_agent_returns_plain_text_choice(monkeypatch):
    class ClarifyingDomainAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    AIMessage(
                        content="Please choose a city for booking:\n1. Shenzhen Innovation Room\n2. Guangzhou Tianhe Room"
                    )
                ]
            }

    def _make_lead_agent(_config):
        return ClarifyingDomainAgent()

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[]))
    monkeypatch.setattr("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent)
    _mcp_initialized.clear()

    async def _run():
        routed = await router_node(
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
                        "result": '{"openId":"ou_123","city":"Shenzhen"}',
                    },
                ],
                "verified_facts": {
                    "helper-1": {
                        "summary": "Organizer resolved",
                        "payload": {"openId": "ou_123", "city": "Shenzhen"},
                    }
                },
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert routed["execution_state"] == "ROUTING_DONE"
        resumed_task = routed["task_pool"][0]
        assert resumed_task["status"] == "RUNNING"

        with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
            executed = await executor_node(
                {
                    "run_id": "run_help123",
                    "task_pool": [resumed_task],
                    "verified_facts": {
                        "helper-1": {
                            "summary": "Organizer resolved",
                            "payload": {"openId": "ou_123", "city": "Shenzhen"},
                        }
                    },
                },
                {"configurable": {}},
            )

        assert executed["execution_state"] == "INTERRUPTED"
        assert executed["task_pool"][0]["status"] == "RUNNING"
        assert executed["task_pool"][0]["status_detail"] == "@waiting_clarification"
        assert executed["task_pool"][0]["clarification_prompt"].startswith("Please choose a city for booking")

    asyncio.run(_run())


def test_route_after_workflow_router_sends_resumed_running_task_to_executor():
    assert route_after_workflow_router(
        {
            "execution_state": "ROUTING_DONE",
            "task_pool": [
                {
                    "task_id": "parent-1",
                    "description": "book a meeting room",
                    "status": "RUNNING",
                    "resolved_inputs": {"helper-1": {"openId": "ou_123"}},
                }
            ],
        }
    ) == "executor"


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
                        "helper_retry_count": 1,
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
        assert task["status_detail"] == "@waiting_clarification"

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
        asyncio.run(_run())


def test_router_retries_direct_helper_once_when_budget_is_exhausted():
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
        assert parent["depends_on_task_ids"] == [helper["task_id"]]
        assert parent["helper_retry_count"] == 1
        assert parent["status_detail"] == "@retrying_helper:contacts-agent"
        assert helper["assigned_agent"] == "contacts-agent"
        assert helper["status"] == "RUNNING"

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

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[]):
        asyncio.run(_run())


def test_router_retries_failed_direct_helper_once_when_hint_is_unambiguous():
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
                            "candidate_agents": ["contacts-agent"],
                        },
                    },
                    {
                        "task_id": "helper-1",
                        "description": "lookup organizer",
                        "run_id": "run_help123",
                        "assigned_agent": "contacts-agent",
                        "status": "FAILED",
                        "error": "Connection error.",
                    },
                ],
                "route_count": 0,
            },
            {"configurable": {}},
        )

        assert result["execution_state"] == "ROUTING_DONE"
        assert len(result["task_pool"]) == 2
        parent = next(task for task in result["task_pool"] if task["task_id"] == "parent-1")
        helper = next(task for task in result["task_pool"] if task["task_id"] != "parent-1")
        assert parent["helper_retry_count"] == 1
        assert parent["depends_on_task_ids"] == [helper["task_id"]]
        assert parent["status_detail"] == "@retrying_helper:contacts-agent"
        assert helper["assigned_agent"] == "contacts-agent"
        assert helper["status"] == "RUNNING"

    with patch("src.agents.router.semantic_router.list_domain_agents", return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")]):
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
                        "helper_retry_count": 1,
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


def test_executor_finishes_system_fallback_with_terminal_message():
    events: list[dict] = []

    async def _run():
        with patch("src.agents.executor.executor.get_stream_writer", return_value=events.append):
            result = await executor_node(
                {
                    "task_pool": [
                        {
                            "task_id": "t1",
                            "description": "Tell the user room booking is unsupported",
                            "run_id": "run_fallback123",
                            "assigned_agent": "SYSTEM_FALLBACK",
                            "status": "RUNNING",
                        }
                    ]
                },
                {"configurable": {}},
            )

        assert result["execution_state"] == "DONE"
        assert result["final_result"] == SYSTEM_FALLBACK_FINAL_MESSAGE
        assert result["messages"][0].content == SYSTEM_FALLBACK_FINAL_MESSAGE
        assert result["workflow_stage"] == "summarizing"
        assert result["workflow_stage_detail"] == SYSTEM_FALLBACK_FINAL_MESSAGE
        assert result["task_pool"][0]["status"] == "DONE"
        assert result["task_pool"][0]["result"] == SYSTEM_FALLBACK_FINAL_MESSAGE
        assert [event["type"] for event in events] == [
            "task_started",
            "task_completed",
            "workflow_stage_changed",
        ]
        assert events[1]["result"] == SYSTEM_FALLBACK_FINAL_MESSAGE
        assert events[2]["workflow_stage"] == "summarizing"

    asyncio.run(_run())


def test_executor_does_not_treat_intervention_resume_marker_as_clarification_answer():
    class CapturingAgent:
        def __init__(self):
            self.payload = None

        async def ainvoke(self, payload, config=None):
            self.payload = payload
            return {"messages": [AIMessage(content="done")]}

    agent = CapturingAgent()

    def _make_lead_agent(_config):
        return agent

    async def _run():
        with patch(
            "src.agents.executor.executor.load_agent_config",
            return_value=SimpleNamespace(mcp_servers=[], intervention_policies={}, hitl_keywords=[]),
        ):
            with patch("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent):
                with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
                    result = await executor_node(
                        {
                            "run_id": "run_existing123",
                            "task_pool": [
                                {
                                    "task_id": "task-1",
                                    "description": "book meeting room",
                                    "run_id": "run_existing123",
                                    "assigned_agent": "meeting-agent",
                                    "status": "RUNNING",
                                    "status_detail": "@intervention_resolved",
                                    "intervention_status": "resolved",
                                    "intervention_fingerprint": "fp-1",
                                    "resolved_inputs": {
                                        "intervention_resolution": {
                                            "action_key": "approve",
                                            "payload": {"comment": "ok"},
                                            "resolution_behavior": "resume_current_task",
                                        }
                                    },
                                }
                            ],
                            "verified_facts": {},
                            "messages": [
                                HumanMessage(content="book a room"),
                                HumanMessage(
                                    content="[intervention_resolved] request_id=req-1 action_key=approve"
                                ),
                            ],
                        },
                        {"configurable": {}},
                    )

        assert result["execution_state"] == "EXECUTING_DONE"
        submitted = agent.payload["messages"][0].content
        assert "Resolved dependency inputs" in submitted
        assert "intervention_resolution" in submitted
        assert "User clarification answer" not in submitted
        assert "[intervention_resolved]" not in submitted

    asyncio.run(_run())


def test_domain_agent_tools_expose_request_help_instead_of_ask_clarification():
    tool_names = {tool.name for tool in get_available_tools(is_domain_agent=True)}

    assert "request_help" in tool_names
    assert "ask_clarification" not in tool_names


def test_top_level_tools_keep_ask_clarification_and_hide_request_help():
    tool_names = {tool.name for tool in get_available_tools(is_domain_agent=False)}

    assert "ask_clarification" in tool_names
    assert "request_help" not in tool_names


def test_meeting_agent_prompt_requires_request_help_for_user_choice():
    prompt = apply_prompt_template(agent_name="meeting-agent", is_domain_agent=True)

    assert 'resolution_strategy="user_clarification"' in prompt
    assert "do NOT return plain text like \"请选择一个城市/会议室\"" in prompt


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


def test_router_accepts_content_blocks_from_model():
    class BlockRouterLLM:
        async def ainvoke(self, _messages):
            return DummyResponse([{"type": "text", "text": "<route>contacts-agent</route>"}])

    async def _run():
        with patch("src.agents.router.semantic_router.create_chat_model", return_value=BlockRouterLLM()):
            result = await router_node(
                {
                    "task_pool": [
                        {
                            "task_id": "t1",
                            "description": "lookup employee id",
                            "status": "PENDING",
                        }
                    ]
                },
                {"configurable": {}},
            )
        assert result["execution_state"] == "ROUTING_DONE"
        assert result["task_pool"][0]["assigned_agent"] == "contacts-agent"

    with patch(
        "src.agents.router.semantic_router.list_domain_agents",
        return_value=[SimpleNamespace(name="contacts-agent", description="Lookup employees")],
    ):
        asyncio.run(_run())


def test_executor_mcp_init_failure_marks_task_failed(monkeypatch):
    events: list[dict] = []

    async def _boom(_agent_name):
        raise RuntimeError("mcp init failed")

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=["dummy"]))
    _mcp_initialized.clear()

    async def _run():
        with patch("src.agents.executor.executor._ensure_mcp_ready", new=_boom):
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

        assert result["execution_state"] == "EXECUTING_DONE"
        assert result["task_pool"][0]["status"] == "FAILED"
        assert result["task_pool"][0]["error"] == "mcp init failed"
        assert events[-1]["type"] == "task_failed"

    asyncio.run(_run())


def test_route_after_workflow_executor_ends_on_error():
    assert route_after_workflow_executor({"execution_state": "ERROR"}) == "__end__"
