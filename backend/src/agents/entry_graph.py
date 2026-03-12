from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.agents.executor import executor_node
from src.agents.graph import (
    ensure_domain_agent_mcp_warmup,
    route_after_workflow_executor,
    route_after_workflow_planner,
    route_after_workflow_router,
)
from src.agents.lead_agent import make_lead_agent
from src.agents.orchestration import orchestration_selector_node
from src.agents.planner import planner_node
from src.agents.router import router_node
from src.agents.thread_state import ThreadState


def _route_after_selector(state: ThreadState) -> str:
    if state.get("resolved_orchestration_mode") == "workflow":
        return "workflow_planner"
    return "leader_entry"


def build_entry_graph(config: RunnableConfig | None = None):
    resolved_config = config or {}
    ensure_domain_agent_mcp_warmup()
    graph = StateGraph(ThreadState)

    graph.add_node("orchestration_selector", orchestration_selector_node)
    graph.add_node("leader_entry", make_lead_agent(resolved_config))
    graph.add_node("workflow_planner", planner_node)
    graph.add_node("workflow_router", router_node)
    graph.add_node("workflow_executor", executor_node)

    graph.add_edge(START, "orchestration_selector")
    graph.add_conditional_edges(
        "orchestration_selector",
        _route_after_selector,
        {
            "leader_entry": "leader_entry",
            "workflow_planner": "workflow_planner",
        },
    )
    graph.add_conditional_edges(
        "workflow_planner",
        route_after_workflow_planner,
        {END: END, "router": "workflow_router"},
    )
    graph.add_conditional_edges(
        "workflow_router",
        route_after_workflow_router,
        {END: END, "planner": "workflow_planner", "executor": "workflow_executor"},
    )
    graph.add_conditional_edges(
        "workflow_executor",
        route_after_workflow_executor,
        {END: END, "planner": "workflow_planner", "router": "workflow_router"},
    )
    graph.add_edge("leader_entry", END)
    return graph.compile()
