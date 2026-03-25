"""LangGraph node decorator for span tracing and runtime after-node hooks."""

import functools
import logging
from typing import Any

from src.observability.tracer import span

logger = logging.getLogger(__name__)

# Map node names to their after-node RuntimeHookName.
# Lazily imported to avoid circular imports at module load time.
_AFTER_NODE_HOOK_MAP: dict[str, str] = {
    "planner": "after_planner",
    "router": "after_router",
    "executor": "after_executor",
}


def traced_node(node_name: str):
    """Decorator that wraps an async LangGraph node function with span creation
    and after-node runtime hook execution.

    Creates a span named ``node.{node_name}`` with attributes extracted from
    the state dict (run_id, task_id, route_count, task_pool_size).

    After the node function returns, if an after-node hook is mapped for
    *node_name* and has registered handlers, the hook chain is executed on
    the returned update dict.  If no handlers are registered the update is
    returned as-is (zero-behaviour-change guarantee).
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

                # Extract thread_id from LangGraph config (used by hooks below)
                _thread_id = None
                _config = args[0] if args else kwargs.get("config")
                if isinstance(_config, dict):
                    _thread_id = (_config.get("configurable") or {}).get("thread_id")

                # --- After-node hook execution ---
                hook_value = _AFTER_NODE_HOOK_MAP.get(node_name)
                if hook_value and isinstance(result, dict):
                    # Build hook-specific metadata from state + result
                    _meta = _build_after_node_metadata(node_name, state, result)

                    result = _run_after_node_hook(
                        hook_value,
                        node_name=node_name,
                        state=state,
                        proposed_update=result,
                        thread_id=_thread_id,
                        metadata=_meta,
                    )

                # --- State commit hooks (Slice B) ---
                if isinstance(result, dict) and ("task_pool" in result or "verified_facts" in result):
                    result = _run_state_commit_hooks(
                        result,
                        state=state,
                        node_name=node_name,
                        thread_id=_thread_id,
                    )

                # Strip private executor metadata keys before returning to graph
                if isinstance(result, dict):
                    for _k in list(result):
                        if _k.startswith("_executor_"):
                            result.pop(_k)

                return result

        return wrapper

    return decorator


def _build_after_node_metadata(
    node_name: str,
    state: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build hook-specific metadata per the contract in the MVP spec.

    after_planner:  planner_goal, done, summary, task_pool_changed
    after_router:   selected_task_id, route_count, execution_state
    after_executor: task_id, assigned_agent, outcome_kind, used_fallback
    """
    meta: dict[str, Any] = {"node_name": node_name}

    if node_name == "planner":
        meta["planner_goal"] = state.get("planner_goal", "")
        exec_state = result.get("execution_state", "")
        meta["done"] = exec_state in ("DONE", "ERROR")
        meta["summary"] = result.get("final_result", "")
        meta["task_pool_changed"] = bool(result.get("task_pool"))
    elif node_name == "router":
        task_pool = result.get("task_pool") or state.get("task_pool") or []
        running = [t for t in task_pool if isinstance(t, dict) and t.get("status") == "RUNNING"]
        meta["selected_task_id"] = running[0].get("task_id", "") if running else ""
        meta["route_count"] = state.get("route_count", 0)
        meta["execution_state"] = result.get("execution_state", "")
    elif node_name == "executor":
        task_pool = result.get("task_pool") or []
        if task_pool and isinstance(task_pool[0], dict):
            first_task = task_pool[0]
            meta["task_id"] = first_task.get("task_id", "")
            meta["assigned_agent"] = first_task.get("assigned_agent", "")
        else:
            meta["task_id"] = ""
            meta["assigned_agent"] = ""
        # Use real executor values when available; fall back to task-status derivation
        meta["outcome_kind"] = result.get("_executor_outcome_kind") or (
            task_pool[0].get("status", "") if task_pool and isinstance(task_pool[0], dict) else ""
        )
        meta["used_fallback"] = result.get("_executor_used_fallback", False)

    return meta


def _run_after_node_hook(
    hook_value: str,
    *,
    node_name: str,
    state: dict[str, Any],
    proposed_update: dict[str, Any],
    thread_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the after-node hook if handlers are registered.  Returns the
    (potentially modified) update dict, or the original on empty registry."""
    try:
        from src.agents.hooks import RuntimeHookName, HookExecutionError, run_runtime_hooks
    except ImportError:
        return proposed_update

    try:
        hook_name = RuntimeHookName(hook_value)
    except ValueError:
        return proposed_update

    try:
        return run_runtime_hooks(
            hook_name,
            node_name=node_name,
            state=state if isinstance(state, dict) else {},
            proposed_update=proposed_update,
            run_id=state.get("run_id") if isinstance(state, dict) else None,
            thread_id=thread_id,
            metadata=metadata,
        )
    except HookExecutionError as exc:
        logger.error(
            "[NodeWrapper] After-node hook '%s' error: %s. Returning error state.",
            hook_value, exc,
        )
        return {
            "execution_state": "ERROR",
            "final_result": f"Runtime hook error ({hook_value}): {exc}",
        }


def _run_state_commit_hooks(
    proposed_update: dict[str, Any],
    *,
    state: dict[str, Any],
    node_name: str,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run BEFORE_TASK_POOL_COMMIT / BEFORE_VERIFIED_FACTS_COMMIT via lifecycle helper.

    Returns the (potentially modified) update dict, or an error state on failure.
    Empty registry → original proposed_update returned as-is.
    """
    try:
        from src.agents.hooks.lifecycle import apply_state_commit_hooks
    except ImportError:
        return proposed_update

    try:
        return apply_state_commit_hooks(
            proposed_update=proposed_update,
            state=state if isinstance(state, dict) else {},
            source_path=f"node_wrapper.{node_name}",
            run_id=state.get("run_id") if isinstance(state, dict) else None,
            thread_id=thread_id,
        )
    except Exception as exc:
        logger.error(
            "[NodeWrapper] State commit hook error for node '%s': %s. Returning error state.",
            node_name, exc,
        )
        return {
            "execution_state": "ERROR",
            "final_result": f"Runtime hook error (state_commit at {node_name}): {exc}",
        }
