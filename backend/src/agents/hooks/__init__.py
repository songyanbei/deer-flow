"""Runtime hook harness — public API surface.

Usage::

    from src.agents.hooks import (
        RuntimeHookName,
        RuntimeHookContext,
        RuntimeHookResult,
        RuntimeHookHandler,
        HookDecision,
        runtime_hook_registry,
        run_runtime_hooks,
        HookExecutionError,
        install_default_runtime_hooks,
    )
"""

from .base import (
    HookDecision,
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookName,
    RuntimeHookResult,
)
from .lifecycle import (
    VerifiedFactsClearAllGuardError,
    apply_after_interrupt_resolve,
    apply_before_interrupt_emit,
    apply_state_commit_hooks,
)
from .registry import RuntimeHookRegistry, runtime_hook_registry
from .runner import HookExecutionError, ensure_default_hooks, run_runtime_hooks
from .verification_hooks import install_default_runtime_hooks

__all__ = [
    "HookDecision",
    "HookExecutionError",
    "RuntimeHookContext",
    "RuntimeHookHandler",
    "RuntimeHookName",
    "RuntimeHookRegistry",
    "RuntimeHookResult",
    "VerifiedFactsClearAllGuardError",
    "apply_after_interrupt_resolve",
    "apply_before_interrupt_emit",
    "apply_state_commit_hooks",
    "ensure_default_hooks",
    "install_default_runtime_hooks",
    "run_runtime_hooks",
    "runtime_hook_registry",
]
