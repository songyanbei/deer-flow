"""Tests for LifecycleManager — user deletion, tenant decommission, thread cleanup."""

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.admin.lifecycle_manager import LifecycleManager, LifecycleResult
from src.agents.governance.ledger import GovernanceLedger
from src.agents.memory.queue import MemoryUpdateQueue
from src.gateway.thread_registry import ThreadRegistry


@pytest.fixture
def tmp_registry(tmp_path):
    return ThreadRegistry(registry_file=tmp_path / "registry.json")


@pytest.fixture
def tmp_queue():
    q = MemoryUpdateQueue()
    yield q
    q.clear()


@pytest.fixture
def tmp_ledger(tmp_path):
    return GovernanceLedger(data_dir=str(tmp_path / "governance"))


@pytest.fixture
def manager(tmp_registry, tmp_queue, tmp_ledger):
    return LifecycleManager(registry=tmp_registry, queue=tmp_queue, ledger=tmp_ledger)


class TestDeleteUser:
    def test_removes_threads(self, manager, tmp_registry):
        tmp_registry.register("t1", "tenant-a", user_id="user-1")
        tmp_registry.register("t2", "tenant-a", user_id="user-1")
        tmp_registry.register("t3", "tenant-a", user_id="user-2")

        result = manager.delete_user("tenant-a", "user-1")
        assert result.threads_removed == 2
        assert tmp_registry.list_threads_by_user("tenant-a", "user-1") == []
        assert tmp_registry.list_threads_by_user("tenant-a", "user-2") == ["t3"]

    def test_cancels_pending_memory(self, manager, tmp_queue):
        with patch("src.agents.memory.queue.get_memory_config") as mock_cfg:
            mock_cfg.return_value = SimpleNamespace(enabled=True, debounce_seconds=9999)
            tmp_queue.add("t1", [], tenant_id="tenant-a", user_id="user-1")
            tmp_queue.add("t2", [], tenant_id="tenant-a", user_id="user-2")

        result = manager.delete_user("tenant-a", "user-1")
        assert result.memory_queue_cancelled == 1
        assert tmp_queue.pending_count == 1

    def test_archives_ledger(self, manager, tmp_ledger):
        tmp_ledger.record(
            thread_id="t1", run_id="r1", task_id="task1",
            source_agent="a1", hook_name="h1", source_path="p1",
            risk_level="low", category="test", decision="allow",
            tenant_id="tenant-a", user_id="user-1",
        )
        tmp_ledger.record(
            thread_id="t2", run_id="r2", task_id="task2",
            source_agent="a2", hook_name="h2", source_path="p2",
            risk_level="low", category="test", decision="allow",
            tenant_id="tenant-a", user_id="user-2",
        )

        result = manager.delete_user("tenant-a", "user-1")
        assert result.ledger_entries_removed == 1
        assert tmp_ledger.total_count == 1

    def test_cleans_filesystem(self, manager, tmp_path):
        user_dir = tmp_path / "tenants" / "tenant-a" / "users" / "user-1"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}")

        with patch("src.config.paths.get_paths") as mock_paths:
            paths = MagicMock()
            paths.tenant_user_dir.return_value = user_dir
            mock_paths.return_value = paths

            result = manager.delete_user("tenant-a", "user-1")

        assert result.filesystem_cleaned is True
        assert not user_dir.exists()

    def test_noop_when_nothing_exists(self, manager):
        result = manager.delete_user("nonexistent", "nobody")
        assert result.threads_removed == 0
        assert result.memory_queue_cancelled == 0
        assert result.ledger_entries_removed == 0
        assert result.filesystem_cleaned is False


class TestDecommissionTenant:
    def test_removes_all_tenant_threads(self, manager, tmp_registry):
        tmp_registry.register("t1", "tenant-a", user_id="u1")
        tmp_registry.register("t2", "tenant-a", user_id="u2")
        tmp_registry.register("t3", "tenant-b", user_id="u1")

        result = manager.decommission_tenant("tenant-a")
        assert result.threads_removed == 2
        assert tmp_registry.list_threads("tenant-a") == []
        assert tmp_registry.list_threads("tenant-b") == ["t3"]

    def test_purges_all_tenant_ledger(self, manager, tmp_ledger):
        for i in range(3):
            tmp_ledger.record(
                thread_id=f"t{i}", run_id=f"r{i}", task_id=f"task{i}",
                source_agent="a", hook_name="h", source_path="p",
                risk_level="low", category="test", decision="allow",
                tenant_id="tenant-a", user_id=f"u{i}",
            )
        tmp_ledger.record(
            thread_id="tx", run_id="rx", task_id="taskx",
            source_agent="a", hook_name="h", source_path="p",
            risk_level="low", category="test", decision="allow",
            tenant_id="tenant-b",
        )

        result = manager.decommission_tenant("tenant-a")
        assert result.ledger_entries_removed == 3
        assert tmp_ledger.total_count == 1


class TestCleanupExpiredThreads:
    def test_removes_expired(self, manager, tmp_registry):
        # Register threads — created_at is set automatically to now
        tmp_registry.register("old-1", "tenant-a")
        tmp_registry.register("old-2", "tenant-a")

        # Manually backdate created_at for testing
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        conn = tmp_registry._get_conn()
        conn.execute("UPDATE threads SET created_at = ? WHERE thread_id IN (?, ?)", (old_time, "old-1", "old-2"))
        conn.commit()

        # Register a fresh thread
        tmp_registry.register("new-1", "tenant-a")

        result = manager.cleanup_expired_threads(max_age_seconds=86400 * 7)  # 7 days
        assert result.threads_removed == 2

    def test_noop_when_all_fresh(self, manager, tmp_registry):
        tmp_registry.register("t1", "tenant-a")
        result = manager.cleanup_expired_threads(max_age_seconds=86400)
        assert result.threads_removed == 0
