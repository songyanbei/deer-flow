from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.agents.graph import build_multi_agent_graph
from src.agents.lead_agent import make_lead_agent
from src.agents.orchestration import orchestration_selector_node
from src.agents.thread_state import ThreadState


def _route_after_selector(state: ThreadState) -> str:
    if state.get("resolved_orchestration_mode") == "workflow":
        return "workflow_entry"
    return "leader_entry"


def build_entry_graph(config: RunnableConfig | None = None):
    resolved_config = config or {}
    graph = StateGraph(ThreadState)

    graph.add_node("orchestration_selector", orchestration_selector_node)
    graph.add_node("leader_entry", make_lead_agent(resolved_config))
    graph.add_node("workflow_entry", build_multi_agent_graph(resolved_config))

    graph.add_edge(START, "orchestration_selector")
    graph.add_conditional_edges(
        "orchestration_selector",
        _route_after_selector,
        {
            "leader_entry": "leader_entry",
            "workflow_entry": "workflow_entry",
        },
    )
    graph.add_edge("leader_entry", END)
    graph.add_edge("workflow_entry", END)
    return graph.compile()
