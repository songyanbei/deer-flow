import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from src.agents.thread_state import ThreadDataState
from src.config.paths import Paths, get_paths
from src.gateway.thread_context import ThreadContext
from src.gateway.thread_registry import get_thread_registry

logger = logging.getLogger(__name__)


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

    def _get_thread_paths_ctx(self, ctx: ThreadContext) -> dict[str, str]:
        return {
            "workspace_path": str(self._paths.sandbox_work_dir_ctx(ctx)),
            "uploads_path": str(self._paths.sandbox_uploads_dir_ctx(ctx)),
            "outputs_path": str(self._paths.sandbox_outputs_dir_ctx(ctx)),
        }

    def _create_thread_directories_ctx(self, ctx: ThreadContext) -> dict[str, str]:
        self._paths.ensure_thread_dirs_ctx(ctx)
        return self._get_thread_paths_ctx(ctx)

    def _resolve_context(self, runtime: Runtime) -> ThreadContext:
        """Extract ThreadContext exclusively from ``config.configurable["thread_context"]``.

        This is the **only** accepted identity source.  Both the Gateway
        (``stream_runtime_message``) and ``DeerFlowClient._get_runnable_config()``
        serialize a ``thread_context`` dict into configurable before invoking the
        agent.  No fallback to ``runtime.context`` or individual configurable
        fields is allowed — doing so would create a bypass around the validated
        identity chain.

        Raises:
            ValueError: If ``thread_context`` is missing or malformed.
        """
        cfg: dict = {}
        try:
            cfg = get_config().get("configurable", {})
        except (ImportError, RuntimeError):
            pass

        raw_ctx = cfg.get("thread_context")
        if not raw_ctx or not isinstance(raw_ctx, dict):
            raise ValueError(
                "ThreadDataMiddleware: configurable['thread_context'] is required but missing. "
                "All callers (Gateway, DeerFlowClient, tests) must serialize ThreadContext "
                "into configurable before invoking the agent."
            )
        return ThreadContext.deserialize(raw_ctx)

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        ctx = self._resolve_context(runtime)

        # Register thread → tenant mapping for access control.
        try:
            get_thread_registry().register(ctx.thread_id, ctx.tenant_id, user_id=ctx.user_id)
        except (ValueError, OSError):
            pass  # best-effort; don't block thread execution

        if self._lazy_init:
            paths = self._get_thread_paths_ctx(ctx)
        else:
            paths = self._create_thread_directories_ctx(ctx)
            print(f"Created thread data directories for thread {ctx.thread_id}")

        return {"thread_data": {**paths}}
