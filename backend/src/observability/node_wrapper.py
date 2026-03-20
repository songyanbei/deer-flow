"""LangGraph node decorator for span tracing."""

import functools
from typing import Any

from src.observability.tracer import span


def traced_node(node_name: str):
    """Decorator that wraps an async LangGraph node function with span creation.

    Creates a span named ``node.{node_name}`` with attributes extracted from
    the state dict (run_id, task_id, route_count, task_pool_size).

    The decorator does NOT change the function signature, return value, or
    exception behavior — it only records them on the span.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            attributes: dict[str, Any] = {
                "node": node_name,
            }
            if isinstance(state, dict):
                if state.get("run_id"):
                    attributes["run_id"] = state["run_id"]
                task_pool = state.get("task_pool") or []
                attributes["task_pool_size"] = len(task_pool)
                running = [t for t in task_pool if isinstance(t, dict) and t.get("status") == "RUNNING"]
                if running:
                    attributes["task_id"] = running[0].get("task_id", "")
                if state.get("route_count") is not None:
                    attributes["route_count"] = state["route_count"]

            with span(f"node.{node_name}", attributes=attributes) as s:
                result = await func(state, *args, **kwargs)
                if isinstance(result, dict) and result.get("execution_state"):
                    s.set_attribute("execution_state", result["execution_state"])
                return result

        return wrapper

    return decorator
