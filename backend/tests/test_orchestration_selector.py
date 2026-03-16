from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.orchestration.selector import (
    decide_orchestration,
    orchestration_selector_node,
)


def test_selector_respects_explicit_leader_request():
    decision = decide_orchestration(
        {"messages": [HumanMessage(content="Research this topic")]},
        {"configurable": {"requested_orchestration_mode": "leader"}},
    )

    assert decision["requested_mode"] == "leader"
    assert decision["resolved_mode"] == "leader"
    assert "explicitly requested leader" in decision["reason"]


def test_selector_respects_explicit_workflow_request():
    decision = decide_orchestration(
        {"messages": [HumanMessage(content="Just answer directly")]},
        {"configurable": {"requested_orchestration_mode": "workflow"}},
    )

    assert decision["requested_mode"] == "workflow"
    assert decision["resolved_mode"] == "workflow"
    assert "explicitly requested workflow" in decision["reason"]


def test_selector_routes_structured_auto_request_to_workflow():
    decision = decide_orchestration(
        {
            "messages": [
                HumanMessage(
                    content="Please research the market, compare competitors, and summarize the findings in a report.",
                )
            ]
        },
        {"configurable": {"requested_orchestration_mode": "auto"}},
    )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "workflow"
    assert decision["workflow_score"] >= 3


def test_selector_falls_back_to_leader_for_simple_auto_request():
    decision = decide_orchestration(
        {"messages": [HumanMessage(content="What is the capital of Japan?")]},
        {"configurable": {"requested_orchestration_mode": "auto"}},
    )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "leader"


def test_selector_routes_structured_chinese_auto_request_to_workflow():
    decision = decide_orchestration(
        {
            "messages": [
                HumanMessage(
                    content="请并行调研三个竞品，分别总结优缺点，并形成一份报告。",
                )
            ]
        },
        {"configurable": {"requested_orchestration_mode": "auto"}},
    )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "workflow"
    assert decision["workflow_score"] >= 3


def test_selector_reuses_existing_mode_for_clarification_resume():
    decision = decide_orchestration(
        {
            "messages": [
                HumanMessage(content="Prepare a report and validate the data"),
                AIMessage(
                    content="Please clarify the target region.",
                    name="ask_clarification",
                ),
                HumanMessage(content="Use APAC only."),
            ],
            "requested_orchestration_mode": "auto",
            "resolved_orchestration_mode": "workflow",
            "execution_state": "INTERRUPTED",
            "task_pool": [
                {
                    "task_id": "task-1",
                    "description": "Prepare a report and validate the data",
                    "status": "RUNNING",
                    "clarification_prompt": "Please clarify the target region.",
                }
            ],
        },
        {"configurable": {"requested_orchestration_mode": "auto"}},
    )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "workflow"
    assert "Resume current workflow run" in decision["reason"]


def test_selector_reuses_existing_mode_for_chinese_clarification_resume():
    decision = decide_orchestration(
        {
            "messages": [
                HumanMessage(content="请调研并汇总这个市场。"),
                AIMessage(content="请先澄清目标国家。", name="ask_clarification"),
                HumanMessage(content="只看日本市场。"),
            ],
            "requested_orchestration_mode": "auto",
            "resolved_orchestration_mode": "workflow",
            "execution_state": "INTERRUPTED",
            "task_pool": [
                {
                    "task_id": "task-1",
                    "description": "调研市场",
                    "status": "RUNNING",
                    "clarification_prompt": "请先澄清目标国家。",
                }
            ],
        },
        {"configurable": {"requested_orchestration_mode": "auto"}},
    )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "workflow"
    assert "Resume current workflow run" in decision["reason"]


def test_selector_does_not_resume_from_plain_assistant_text_that_mentions_clarification():
    existing_run_id = "run_existing_plain_text"
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
                        "description": "Prepare the report",
                        "status": "RUNNING",
                        "clarification_prompt": "Which region should I focus on?",
                    }
                ],
                "messages": [
                    HumanMessage(content="Prepare the report"),
                    AIMessage(
                        content="Need clarification: which region should I focus on?",
                        name="assistant",
                    ),
                    HumanMessage(content="Japan only."),
                ],
            },
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

    assert result["run_id"] != existing_run_id
    assert result["workflow_stage"] == "acknowledged"
    assert events[0]["run_id"] == result["run_id"]
    assert events[1]["run_id"] == result["run_id"]


def test_selector_starts_new_run_when_user_redirects_after_clarification():
    existing_run_id = "run_existing_redirect"
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
                        "clarification_prompt": "Which building should I book?",
                    }
                ],
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
            },
            {"configurable": {"requested_orchestration_mode": "workflow"}},
        )

    assert result["run_id"] != existing_run_id
    assert result["workflow_stage"] == "acknowledged"
    assert events[0]["run_id"] == result["run_id"]
    assert events[1]["run_id"] == result["run_id"]


def test_selector_uses_agent_default_mode_before_auto():
    with patch(
        "src.agents.orchestration.selector.load_agent_config",
        return_value=SimpleNamespace(requested_orchestration_mode="workflow"),
    ):
        decision = decide_orchestration(
            {"messages": [HumanMessage(content="Do something simple")]},
            {
                "configurable": {
                    "requested_orchestration_mode": "auto",
                    "agent_name": "planner-agent",
                }
            },
        )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "workflow"
    assert "Agent default routed to workflow" == decision["reason"]


def test_selector_node_emits_mode_patch_event():
    events: list[dict] = []

    with patch(
        "src.agents.orchestration.selector.get_stream_writer",
        return_value=events.append,
    ):
        result = orchestration_selector_node(
            {
                "messages": [
                    HumanMessage(
                        content="Please research the market, compare competitors, and summarize the findings in a report.",
                    )
                ]
            },
            {"configurable": {"requested_orchestration_mode": "auto"}},
        )

    assert result["resolved_orchestration_mode"] == "workflow"
    assert result["run_id"].startswith("run_")
    assert events[0] == {
        "type": "orchestration_mode_resolved",
        "requested_orchestration_mode": "auto",
        "resolved_orchestration_mode": "workflow",
        "orchestration_reason": "Detected structured or multi-step task; routed to workflow",
        "run_id": result["run_id"],
    }
    assert events[1]["type"] == "workflow_stage_changed"
    assert events[1]["workflow_stage"] == "acknowledged"
    assert events[1]["run_id"] == result["run_id"]
