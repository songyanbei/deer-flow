"""Lifecycle Manager — orchestrates user deletion, tenant decommission, and thread cleanup.

Coordinates across ThreadRegistry, MemoryQueue, GovernanceLedger, MCP cache,
and filesystem paths to ensure complete data removal for lifecycle operations.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.governance.ledger import GovernanceLedger
    from src.agents.memory.queue import MemoryUpdateQueue
    from src.gateway.thread_registry import ThreadRegistry

logger = logging.getLogger(__name__)


@dataclass
class LifecycleResult:
    """Summary of a lifecycle operation."""

    threads_removed: int = 0
    sandbox_states_cleaned: int = 0
    memory_queue_cancelled: int = 0
    ledger_entries_removed: int = 0
    mcp_scopes_unloaded: bool = False
    filesystem_cleaned: bool = False
    errors: list[str] | None = None

    def add_error(self, step: str, exc: Exception) -> None:
        """Record a step failure for compensation logging."""
        if self.errors is None:
            self.errors = []
        self.errors.append(f"{step}: {type(exc).__name__}: {exc}")

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


class LifecycleManager:
    """Orchestrator for multi-tenant lifecycle operations."""

    @staticmethod
    def _stop_sandbox(thread_id: str, result: LifecycleResult) -> None:
        """Best-effort stop of a running sandbox for a thread.

        Must be called **before** deleting sandbox_state and thread data so
        the running container doesn't write back into a cleaned directory.
        """
        try:
            from src.sandbox.sandbox_provider import get_sandbox_provider
            provider = get_sandbox_provider()
            provider.release_by_thread(thread_id)
        except Exception as exc:
            result.add_error(f"sandbox_stop:{thread_id}", exc)
            logger.debug("Best-effort sandbox stop failed for thread %s: %s", thread_id, exc)

    def _cleanup_sandbox_states(self, thread_ids: list[str], result: LifecycleResult) -> int:
        """Stop running sandboxes and clean state directories for a list of thread IDs.

        Cleanup order: stop container → delete state directory.
        Best-effort — failures are logged to ``result.errors`` but don't
        stop processing.

        Returns:
            Number of sandbox state directories successfully cleaned.
        """
        from src.config.paths import get_paths
        paths = get_paths()
        cleaned = 0
        for tid in thread_ids:
            # Stop running sandbox before deleting its state
            self._stop_sandbox(tid, result)

            sandbox_state_dir = paths.sandbox_state_dir(tid)
            if sandbox_state_dir.exists():
                try:
                    shutil.rmtree(sandbox_state_dir)
                    cleaned += 1
                except OSError as exc:
                    result.add_error(f"sandbox_state_cleanup:{tid}", exc)
                    logger.debug("Failed to remove sandbox state: %s", sandbox_state_dir)
        return cleaned

    def __init__(
        self,
        registry: ThreadRegistry | None = None,
        queue: MemoryUpdateQueue | None = None,
        ledger: GovernanceLedger | None = None,
    ) -> None:
        if registry is None:
            from src.gateway.thread_registry import get_thread_registry
            registry = get_thread_registry()
        if queue is None:
            from src.agents.memory.queue import get_memory_queue
            queue = get_memory_queue()
        if ledger is None:
            from src.agents.governance.ledger import governance_ledger
            ledger = governance_ledger
        self._registry = registry
        self._queue = queue
        self._ledger = ledger

    def delete_user(self, tenant_id: str, user_id: str) -> LifecycleResult:
        """Delete all data belonging to a specific user within a tenant.

        Cleanup order (file-first, registry-last):
        1. Enumerate user threads (need registry to find them)
        2. Stop sandboxes → delete sandbox_state dirs
        3. Cancel pending memory updates
        4. Archive governance ledger entries
        5. Delete user filesystem directory (thread data lives here)
        6. Delete threads from registry (only after files are gone)

        Individual step failures are recorded in ``result.errors`` so the
        caller can inspect and retry.  Later steps still execute even if
        earlier ones fail.
        """
        result = LifecycleResult()

        # 1. Enumerate threads first (must happen before registry deletion)
        thread_ids: list[str] = []
        try:
            thread_ids = self._registry.list_threads_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("list_threads_for_sandbox_cleanup", exc)
            logger.exception("delete_user: failed to list threads for sandbox cleanup %s/%s", tenant_id, user_id)

        # 2. Stop sandboxes → clean sandbox_state dirs
        result.sandbox_states_cleaned = self._cleanup_sandbox_states(thread_ids, result)

        # 3. Cancel pending memory updates
        try:
            result.memory_queue_cancelled = self._queue.cancel_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("cancel_by_user", exc)
            logger.exception("delete_user: failed to cancel queue for %s/%s", tenant_id, user_id)

        # 4. Archive governance ledger entries
        try:
            result.ledger_entries_removed = self._ledger.archive_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("archive_by_user", exc)
            logger.exception("delete_user: failed to archive ledger for %s/%s", tenant_id, user_id)

        # 5. Delete user filesystem directory (contains all thread data)
        from src.config.paths import get_paths
        paths = get_paths()
        user_dir = paths.tenant_user_dir(tenant_id, user_id)
        if user_dir.exists():
            try:
                shutil.rmtree(user_dir)
                result.filesystem_cleaned = True
            except OSError as exc:
                result.add_error("filesystem_cleanup", exc)
                logger.warning("Failed to remove user directory: %s", user_dir)

        # 6. Registry deletion LAST — only after files are cleaned
        try:
            result.threads_removed = self._registry.delete_threads_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("delete_threads_by_user", exc)
            logger.exception("delete_user: failed to delete threads for %s/%s", tenant_id, user_id)

        logger.info(
            "delete_user tenant=%s user=%s: threads=%d sandbox_states=%d queue=%d ledger=%d fs=%s errors=%s",
            tenant_id, user_id,
            result.threads_removed, result.sandbox_states_cleaned,
            result.memory_queue_cancelled, result.ledger_entries_removed,
            result.filesystem_cleaned, result.errors,
        )
        return result

    def decommission_tenant(self, tenant_id: str) -> LifecycleResult:
        """Decommission an entire tenant — remove all threads, queue items, ledger entries, and MCP state.

        Cleanup order (file-first, registry-last):
        1. Enumerate tenant threads (need registry to find them)
        2. Stop sandboxes → delete sandbox_state dirs
        3. Cancel pending memory updates
        4. Purge governance ledger entries
        5. Invalidate MCP tenant cache/scopes
        6. Delete tenant filesystem directory
        7. Delete threads from registry (only after files are gone)

        Individual step failures are recorded in ``result.errors``.
        """
        result = LifecycleResult()

        # 1. Enumerate threads (must happen before registry deletion)
        thread_ids: list[str] = []
        try:
            thread_ids = self._registry.list_threads(tenant_id)
        except Exception as exc:
            result.add_error("list_threads_for_sandbox_cleanup", exc)
            logger.exception("decommission_tenant: failed to list threads for sandbox cleanup %s", tenant_id)

        # 2. Stop sandboxes → clean sandbox_state dirs
        result.sandbox_states_cleaned = self._cleanup_sandbox_states(thread_ids, result)

        # 3. Cancel pending memory updates
        try:
            result.memory_queue_cancelled = self._queue.cancel_by_tenant(tenant_id)
        except Exception as exc:
            result.add_error("cancel_by_tenant", exc)
            logger.exception("decommission_tenant: failed to cancel queue for %s", tenant_id)

        # 4. Purge governance ledger entries
        try:
            result.ledger_entries_removed = self._ledger.purge_by_tenant(tenant_id)
        except Exception as exc:
            result.add_error("purge_by_tenant", exc)
            logger.exception("decommission_tenant: failed to purge ledger for %s", tenant_id)

        # 5. MCP cleanup (best-effort, imports may fail if MCP not configured)
        try:
            from src.mcp.cache import invalidate_tenant
            invalidate_tenant(tenant_id)
            result.mcp_scopes_unloaded = True
        except Exception as exc:
            result.add_error("mcp_cache_invalidate", exc)
            logger.debug("MCP cache invalidation skipped for tenant %s", tenant_id)

        try:
            from src.mcp.runtime_manager import unload_tenant_scopes
            unload_tenant_scopes(tenant_id)
            result.mcp_scopes_unloaded = True
        except Exception as exc:
            result.add_error("mcp_scope_unload", exc)
            logger.debug("MCP scope unload skipped for tenant %s", tenant_id)

        # 6. Delete tenant filesystem directory
        from src.config.paths import get_paths
        paths = get_paths()
        tenant_dir = paths.tenant_dir(tenant_id)
        if tenant_dir.exists():
            try:
                shutil.rmtree(tenant_dir)
                result.filesystem_cleaned = True
            except OSError as exc:
                result.add_error("filesystem_cleanup", exc)
                logger.warning("Failed to remove tenant directory: %s", tenant_dir)

        # 7. Registry deletion LAST — only after files are cleaned
        try:
            result.threads_removed = self._registry.delete_threads_by_tenant(tenant_id)
        except Exception as exc:
            result.add_error("delete_threads_by_tenant", exc)
            logger.exception("decommission_tenant: failed to delete threads for %s", tenant_id)

        logger.info(
            "decommission_tenant tenant=%s: threads=%d sandbox_states=%d queue=%d ledger=%d mcp=%s fs=%s errors=%s",
            tenant_id,
            result.threads_removed, result.sandbox_states_cleaned,
            result.memory_queue_cancelled, result.ledger_entries_removed,
            result.mcp_scopes_unloaded, result.filesystem_cleaned, result.errors,
        )
        return result

    def cleanup_expired_threads(self, max_age_seconds: int = 86400 * 7, tenant_id: str | None = None) -> LifecycleResult:
        """Remove threads older than *max_age_seconds*.

        Cleanup order per thread (file-first, registry-last):
        1. Get binding (need owner info for tenant-scoped paths)
        2. Stop sandbox → delete sandbox_state
        3. Delete tenant-scoped thread directory
        4. Delete legacy flat thread directory
        5. Unregister from registry (only after all files are gone)

        Args:
            max_age_seconds: Maximum thread age (default 7 days).
            tenant_id: If provided, only clean up threads belonging to this tenant.
        """
        from src.config.paths import get_paths

        result = LifecycleResult()
        expired = self._registry.list_expired_threads(max_age_seconds, tenant_id=tenant_id)
        paths = get_paths()

        for tid in expired:
            # 1. Get binding before any cleanup (need owner info for paths)
            binding = self._registry.get_binding(tid)

            # 2. Stop running sandbox, then clean sandbox state
            self._stop_sandbox(tid, result)
            sandbox_state_dir = paths.sandbox_state_dir(tid)
            if sandbox_state_dir.exists():
                try:
                    shutil.rmtree(sandbox_state_dir)
                    result.sandbox_states_cleaned += 1
                except OSError as exc:
                    result.add_error(f"sandbox_state_cleanup:{tid}", exc)

            # 3. Clean up per-thread filesystem directory using tenant/user path
            if binding and isinstance(binding, dict):
                owner_tenant = binding.get("tenant_id")
                owner_user = binding.get("user_id")
                if owner_tenant and owner_user:
                    thread_dir = paths.tenant_user_thread_dir(owner_tenant, owner_user, tid)
                    if thread_dir.exists():
                        try:
                            shutil.rmtree(thread_dir)
                        except OSError as exc:
                            result.add_error(f"thread_dir_cleanup:{tid}", exc)

            # 4. Also clean legacy flat path if it exists
            legacy_dir = paths.thread_dir(tid)
            if legacy_dir.exists():
                try:
                    shutil.rmtree(legacy_dir)
                except OSError as exc:
                    result.add_error(f"legacy_dir_cleanup:{tid}", exc)

            # 5. Unregister LAST — only after all files are cleaned
            self._registry.unregister(tid)

        result.threads_removed = len(expired)
        if expired:
            result.filesystem_cleaned = True

        logger.info("cleanup_expired_threads tenant=%s max_age=%ds: removed=%d", tenant_id, max_age_seconds, result.threads_removed)
        return result
