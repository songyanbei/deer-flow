from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.agents.thread_state import SandboxState, ThreadDataState
from src.sandbox import get_sandbox_provider


class SandboxMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]


class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """Create a sandbox environment and assign it to an agent.

    Lifecycle Management:
    - With lazy_init=True (default): Sandbox is acquired on first tool call
    - With lazy_init=False: Sandbox is acquired on first agent invocation (before_agent)
    - Sandbox is reused across multiple turns within the same thread
    - Sandbox is NOT released after each agent call to avoid wasteful recreation
    - Cleanup happens at application shutdown via SandboxProvider.shutdown()
    """

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True):
        """Initialize sandbox middleware.

        Args:
            lazy_init: If True, defer sandbox acquisition until first tool call.
                      If False, acquire sandbox eagerly in before_agent().
                      Default is True for optimal performance.
        """
        super().__init__()
        self._lazy_init = lazy_init

    def _acquire_sandbox(self, thread_id: str, runtime: Runtime) -> str:
        import logging
        _logger = logging.getLogger(__name__)
        provider = get_sandbox_provider()

        # Pass ThreadContext to provider exclusively from configurable["thread_context"].
        # No fallback to runtime.context or individual configurable fields — all callers
        # (Gateway, DeerFlowClient) must serialize thread_context before invocation.
        try:
            import os
            from langgraph.config import get_config
            from src.gateway.thread_context import ThreadContext
            raw_ctx = get_config().get("configurable", {}).get("thread_context")
            if raw_ctx and isinstance(raw_ctx, dict):
                ctx = ThreadContext.deserialize(raw_ctx)
                provider.set_thread_context(thread_id, ctx)
            else:
                _oidc_enabled = os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes")
                if _oidc_enabled:
                    raise RuntimeError(
                        f"SandboxMiddleware: configurable['thread_context'] is required when "
                        f"OIDC is enabled, but missing for thread {thread_id}. All callers "
                        f"must serialize ThreadContext into configurable before invocation."
                    )
                _logger.warning(
                    "SandboxMiddleware: configurable['thread_context'] missing for thread %s — "
                    "sandbox will use legacy mount paths (OIDC disabled, dev mode).",
                    thread_id,
                )
        except RuntimeError:
            raise  # re-raise OIDC enforcement errors
        except Exception:
            _logger.debug("SandboxMiddleware: failed to read thread_context for thread %s", thread_id, exc_info=True)

        sandbox_id = provider.acquire(thread_id)
        print(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return super().before_agent(state, runtime)

        # Eager initialization (original behavior)
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = runtime.context["thread_id"]
            print(f"Thread ID: {thread_id}")
            sandbox_id = self._acquire_sandbox(thread_id, runtime)
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return super().before_agent(state, runtime)
