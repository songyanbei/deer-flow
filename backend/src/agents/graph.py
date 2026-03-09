"""Multi-agent LangGraph graph - Phase 1 implementation."""

import asyncio
import logging

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.agents.executor import executor_node
from src.agents.planner import planner_node
from src.agents.router import router_node
from src.agents.thread_state import ThreadState
from src.config.agents_config import list_domain_agents

logger = logging.getLogger(__name__)

_TERMINAL_STATES = {"DONE", "ERROR"}
_mcp_warmup_started = False


def _route_after_planner(state: ThreadState) -> str:
    """Decide where to go after planner_node runs."""
    exec_state = state.get("execution_state") or ""
    if exec_state in _TERMINAL_STATES:
        logger.debug("[Graph] planner -> END (execution_state=%s)", exec_state)
        return END
    logger.debug("[Graph] planner -> router (execution_state=%s)", exec_state)
    return "router"


def _route_after_router(state: ThreadState) -> str:
    """Decide where to go after router_node runs."""
    exec_state = state.get("execution_state") or ""
    if exec_state in _TERMINAL_STATES:
        logger.debug("[Graph] router -> END (execution_state=%s)", exec_state)
        return END
    if exec_state == "PLANNING_NEEDED":
        logger.debug("[Graph] router -> planner (PLANNING_NEEDED)")
        return "planner"
    logger.debug("[Graph] router -> executor")
    return "executor"


def _route_after_executor(state: ThreadState) -> str:
    """Decide where to go after executor_node runs."""
    exec_state = state.get("execution_state") or ""
    if exec_state == "INTERRUPTED":
        logger.debug("[Graph] executor -> END (INTERRUPTED)")
        return END
    logger.debug("[Graph] executor -> planner")
    return "planner"


def _compile_multi_agent_graph(checkpointer=None):
    graph = StateGraph(ThreadState)

    graph.add_node("planner", planner_node)
    graph.add_node("router", router_node)
    graph.add_node("executor", executor_node)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", _route_after_planner, {END: END, "router": "router"})
    graph.add_conditional_edges("router", _route_after_router, {END: END, "planner": "planner", "executor": "executor"})
    graph.add_conditional_edges("executor", _route_after_executor, {END: END, "planner": "planner"})

    return graph.compile(checkpointer=checkpointer)


async def _warmup_domain_agent_mcp() -> None:
    agents = [agent for agent in list_domain_agents() if agent.mcp_servers]
    if not agents:
        return

    from src.execution.mcp_pool import mcp_pool

    results = await asyncio.gather(
        *(mcp_pool.init_agent_connections(agent.name, [server.model_dump() for server in agent.mcp_servers]) for agent in agents),
        return_exceptions=True,
    )
    for agent, result in zip(agents, results, strict=False):
        if isinstance(result, Exception):
            logger.warning("[Graph] MCP warmup failed for agent '%s': %s", agent.name, result)
        elif result is False:
            logger.warning("[Graph] MCP warmup failed for agent '%s': %s", agent.name, mcp_pool.get_agent_error(agent.name) or "unknown error")
        else:
            logger.info("[Graph] MCP warmup succeeded for agent '%s'.", agent.name)


def _ensure_domain_agent_mcp_warmup() -> None:
    global _mcp_warmup_started
    if _mcp_warmup_started:
        return
    _mcp_warmup_started = True

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_warmup_domain_agent_mcp())
    else:
        loop.create_task(_warmup_domain_agent_mcp())


def build_multi_agent_graph(config: RunnableConfig | None = None):
    """LangGraph Server factory for the multi-agent graph."""
    _ = config
    _ensure_domain_agent_mcp_warmup()
    return _compile_multi_agent_graph()


def build_multi_agent_graph_for_test(checkpointer=None):
    """Test helper that allows explicit checkpointer injection."""
    return _compile_multi_agent_graph(checkpointer=checkpointer)
