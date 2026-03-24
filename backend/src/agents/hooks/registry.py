"""Runtime hook registry: thread-safe registration, lookup, and lifecycle management."""

from __future__ import annotations

import logging
import threading
from typing import Any

from .base import RuntimeHookHandler, RuntimeHookName

logger = logging.getLogger(__name__)


class RuntimeHookRegistry:
    """Central registry for runtime hook handlers.

    Handlers are stored per hook-name and sorted by ``(priority, insertion_order)``
    so that execution order is deterministic.

    Thread safety is guaranteed by an internal lock.  The registry is designed
    to be instantiated once at process level and shared across all graph runs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # hook_name -> list of (priority, insertion_order, handler)
        self._handlers: dict[RuntimeHookName, list[tuple[int, int, RuntimeHookHandler]]] = {}
        self._insertion_counter: int = 0

    # -- Registration --------------------------------------------------------

    def register(
        self,
        hook_name: RuntimeHookName,
        handler: RuntimeHookHandler,
        *,
        priority: int | None = None,
    ) -> None:
        """Register *handler* for *hook_name*.

        *priority* overrides ``handler.priority`` when given.  Lower values
        execute first.
        """
        effective_priority = priority if priority is not None else handler.priority
        with self._lock:
            order = self._insertion_counter
            self._insertion_counter += 1
            entry = (effective_priority, order, handler)
            self._handlers.setdefault(hook_name, []).append(entry)
            # Keep sorted by (priority, insertion_order)
            self._handlers[hook_name].sort(key=lambda e: (e[0], e[1]))
        logger.debug(
            "[HookRegistry] Registered handler '%s' for hook '%s' with priority=%d",
            handler.name, hook_name.value, effective_priority,
        )

    # -- Lookup --------------------------------------------------------------

    def get_handlers(self, hook_name: RuntimeHookName) -> list[RuntimeHookHandler]:
        """Return handlers for *hook_name* in execution order (priority, then insertion)."""
        with self._lock:
            entries = self._handlers.get(hook_name, [])
            return [handler for _, _, handler in entries]

    def has_handlers(self, hook_name: RuntimeHookName) -> bool:
        """Return ``True`` if at least one handler is registered for *hook_name*."""
        with self._lock:
            return bool(self._handlers.get(hook_name))

    def has_handler_named(self, hook_name: RuntimeHookName, handler_name: str) -> bool:
        """Return ``True`` if a handler with exactly *handler_name* is registered for *hook_name*."""
        with self._lock:
            entries = self._handlers.get(hook_name, [])
            return any(h.name == handler_name for _, _, h in entries)

    # -- Lifecycle -----------------------------------------------------------

    def clear(self, hook_name: RuntimeHookName | None = None) -> None:
        """Remove registered handlers.

        If *hook_name* is given only that slot is cleared; otherwise the entire
        registry is reset.  Intended for testing and hot-reload scenarios.
        """
        with self._lock:
            if hook_name is not None:
                self._handlers.pop(hook_name, None)
            else:
                self._handlers.clear()
                self._insertion_counter = 0
        logger.debug(
            "[HookRegistry] Cleared %s",
            f"hook '{hook_name.value}'" if hook_name else "all hooks",
        )

    # -- Introspection -------------------------------------------------------

    def list_hooks(self) -> dict[str, list[dict[str, Any]]]:
        """Return a JSON-friendly snapshot of all registrations.

        Useful for debugging, admin endpoints, and test assertions.
        """
        with self._lock:
            result: dict[str, list[dict[str, Any]]] = {}
            for hook_name, entries in self._handlers.items():
                result[hook_name.value] = [
                    {
                        "handler_name": handler.name,
                        "priority": priority,
                        "insertion_order": order,
                    }
                    for priority, order, handler in entries
                ]
            return result

    def __repr__(self) -> str:
        with self._lock:
            total = sum(len(v) for v in self._handlers.values())
        return f"<RuntimeHookRegistry hooks={len(self._handlers)} handlers={total}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

runtime_hook_registry = RuntimeHookRegistry()
