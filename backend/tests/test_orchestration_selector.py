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
                AIMessage(content="Please clarify the target region.", name="ask_clarification"),
                HumanMessage(content="Use APAC only."),
            ],
            "requested_orchestration_mode": "auto",
            "resolved_orchestration_mode": "workflow",
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
                AIMessage(content="请先澄清目标国家。", name="assistant"),
                HumanMessage(content="只看日本市场。"),
            ],
            "requested_orchestration_mode": "auto",
            "resolved_orchestration_mode": "workflow",
        },
        {"configurable": {"requested_orchestration_mode": "auto"}},
    )

    assert decision["requested_mode"] == "auto"
    assert decision["resolved_mode"] == "workflow"
    assert "Resume current workflow run" in decision["reason"]


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
    assert events == [
        {
            "type": "orchestration_mode_resolved",
            "requested_orchestration_mode": "auto",
            "resolved_orchestration_mode": "workflow",
            "orchestration_reason": "Detected structured or multi-step task; routed to workflow",
        }
    ]
