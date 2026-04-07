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

        Steps (best-effort with compensation log):
        1. Remove threads from registry
        2. Cancel pending memory updates
        3. Archive governance ledger entries
        4. Remove user filesystem directory

        Individual step failures are recorded in ``result.errors`` so the
        caller can inspect and retry.  Later steps still execute even if
        earlier ones fail.
        """
        result = LifecycleResult()

        try:
            result.threads_removed = self._registry.delete_threads_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("delete_threads_by_user", exc)
            logger.exception("delete_user: failed to delete threads for %s/%s", tenant_id, user_id)

        try:
            result.memory_queue_cancelled = self._queue.cancel_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("cancel_by_user", exc)
            logger.exception("delete_user: failed to cancel queue for %s/%s", tenant_id, user_id)

        try:
            result.ledger_entries_removed = self._ledger.archive_by_user(tenant_id, user_id)
        except Exception as exc:
            result.add_error("archive_by_user", exc)
            logger.exception("delete_user: failed to archive ledger for %s/%s", tenant_id, user_id)

        # Clean up user filesystem data
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

        logger.info(
            "delete_user tenant=%s user=%s: threads=%d queue=%d ledger=%d fs=%s errors=%s",
            tenant_id, user_id,
            result.threads_removed, result.memory_queue_cancelled,
            result.ledger_entries_removed, result.filesystem_cleaned,
            result.errors,
        )
        return result

    def decommission_tenant(self, tenant_id: str) -> LifecycleResult:
        """Decommission an entire tenant — remove all threads, queue items, ledger entries, and MCP state.

        Steps (best-effort with compensation log):
        1. Remove all threads from registry
        2. Cancel all pending memory updates
        3. Purge all governance ledger entries
        4. Invalidate MCP tenant cache/scopes
        5. Remove tenant filesystem directory

        Individual step failures are recorded in ``result.errors``.
        """
        result = LifecycleResult()

        try:
            result.threads_removed = self._registry.delete_threads_by_tenant(tenant_id)
        except Exception as exc:
            result.add_error("delete_threads_by_tenant", exc)
            logger.exception("decommission_tenant: failed to delete threads for %s", tenant_id)

        try:
            result.memory_queue_cancelled = self._queue.cancel_by_tenant(tenant_id)
        except Exception as exc:
            result.add_error("cancel_by_tenant", exc)
            logger.exception("decommission_tenant: failed to cancel queue for %s", tenant_id)

        try:
            result.ledger_entries_removed = self._ledger.purge_by_tenant(tenant_id)
        except Exception as exc:
            result.add_error("purge_by_tenant", exc)
            logger.exception("decommission_tenant: failed to purge ledger for %s", tenant_id)

        # MCP cleanup (best-effort, imports may fail if MCP not configured)
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

        # Clean up tenant filesystem data
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

        logger.info(
            "decommission_tenant tenant=%s: threads=%d queue=%d ledger=%d mcp=%s fs=%s errors=%s",
            tenant_id,
            result.threads_removed, result.memory_queue_cancelled,
            result.ledger_entries_removed, result.mcp_scopes_unloaded,
            result.filesystem_cleaned, result.errors,
        )
        return result

    def cleanup_expired_threads(self, max_age_seconds: int = 86400 * 7, tenant_id: str | None = None) -> LifecycleResult:
        """Remove threads older than *max_age_seconds*.

        Args:
            max_age_seconds: Maximum thread age (default 7 days).
            tenant_id: If provided, only clean up threads belonging to this tenant.
        """
        from src.config.paths import get_paths

        result = LifecycleResult()
        expired = self._registry.list_expired_threads(max_age_seconds, tenant_id=tenant_id)
        paths = get_paths()
        for tid in expired:
            self._registry.unregister(tid)
            # Clean up per-thread filesystem directory
            thread_dir = paths.thread_dir(tid)
            if thread_dir.exists():
                try:
                    shutil.rmtree(thread_dir)
                except OSError:
                    logger.debug("Failed to remove thread directory: %s", thread_dir)
        result.threads_removed = len(expired)
        if expired:
            result.filesystem_cleaned = True

        logger.info("cleanup_expired_threads tenant=%s max_age=%ds: removed=%d", tenant_id, max_age_seconds, result.threads_removed)
        return result
