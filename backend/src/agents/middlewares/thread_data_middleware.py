from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from src.agents.thread_state import ThreadDataState
from src.config.paths import Paths, get_paths
from src.gateway.thread_registry import get_thread_registry


class ThreadDataMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """Create thread data directories for each thread execution."""

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()
        self._lazy_init = lazy_init

    def _get_thread_paths(self, thread_id: str) -> dict[str, str]:
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id)),
        }

    def _create_thread_directories(self, thread_id: str) -> dict[str, str]:
        self._paths.ensure_thread_dirs(thread_id)
        return self._get_thread_paths(thread_id)

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        # LangGraph Server injects thread_id via runtime.context (HTTP request metadata).
        # When invoking the graph directly (tests, executor sub-calls), fall back to
        # get_config() which reads the current LangGraph run config (always available).
        thread_id = None
        if runtime.context is not None:
            thread_id = runtime.context.get("thread_id")
        if thread_id is None:
            try:
                thread_id = get_config().get("configurable", {}).get("thread_id")
            except Exception:
                pass
        if thread_id is None:
            raise ValueError("Thread ID is required in the context")

        # Register thread → tenant mapping for access control.
        # Priority: runtime.context (set by Gateway) → configurable (set by
        # runtime_service / embedded client) → "default" (single-tenant mode).
        tenant_id = None
        user_id = None
        if runtime.context is not None:
            tenant_id = runtime.context.get("tenant_id")
            user_id = runtime.context.get("user_id")
        if not tenant_id:
            try:
                cfg = get_config().get("configurable", {})
                tenant_id = tenant_id or cfg.get("tenant_id", "default")
                user_id = user_id or cfg.get("user_id")
            except Exception:
                tenant_id = "default"
        try:
            get_thread_registry().register(thread_id, tenant_id, user_id=user_id)
        except Exception:
            pass  # best-effort; don't block thread execution

        if self._lazy_init:
            paths = self._get_thread_paths(thread_id)
        else:
            paths = self._create_thread_directories(thread_id)
            print(f"Created thread data directories for thread {thread_id}")

        return {"thread_data": {**paths}}
