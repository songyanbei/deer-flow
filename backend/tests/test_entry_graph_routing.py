from __future__ import annotations

import asyncio
from unittest.mock import patch

from langchain_core.messages import HumanMessage


async def _leader_node(_state, _config=None):
    return {"final_result": "leader-branch"}


async def _workflow_node(_state, _config=None):
    return {"final_result": "workflow-branch"}


def test_entry_graph_routes_to_leader_branch():
    async def _run():
        from src.agents.entry_graph import build_entry_graph

        with (
            patch("src.agents.entry_graph.make_lead_agent", return_value=_leader_node),
            patch("src.agents.entry_graph.build_multi_agent_graph", return_value=_workflow_node),
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
            patch("src.agents.entry_graph.make_lead_agent", return_value=_leader_node),
            patch("src.agents.entry_graph.build_multi_agent_graph", return_value=_workflow_node),
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
            patch("src.agents.entry_graph.make_lead_agent", return_value=_leader_node),
            patch("src.agents.entry_graph.build_multi_agent_graph", return_value=_workflow_node),
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
