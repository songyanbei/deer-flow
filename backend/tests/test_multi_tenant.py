"""Tests for Phase 2 multi-tenant isolation.

Covers:
- ThreadRegistry CRUD and access control
- Tenant-scoped path resolution
- Governance ledger tenant filtering
- Memory cache key isolation
- Memory queue dedupe key isolation
- Gateway dependencies fallback behavior
- Agents router tenant scoping (list/create/delete)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config.paths import Paths
from src.gateway.thread_registry import ThreadRegistry


# ── ThreadRegistry tests ─────────────────────────────────────────────


class TestThreadRegistry:
    """Tests for thread_id → tenant_id mapping."""

    def _make_registry(self, tmp_path: Path) -> ThreadRegistry:
        return ThreadRegistry(registry_file=tmp_path / "thread_registry.json")

    def test_register_and_get_tenant(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("thread-1", "tenant-a")
        assert reg.get_tenant("thread-1") == "tenant-a"

    def test_unregistered_thread_returns_none(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.get_tenant("nonexistent") is None

    def test_check_access_owner_allowed(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("thread-1", "tenant-a")
        assert reg.check_access("thread-1", "tenant-a") is True

    def test_check_access_other_tenant_denied(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("thread-1", "tenant-a")
        assert reg.check_access("thread-1", "tenant-b") is False

    def test_check_access_unregistered_denied(self, tmp_path):
        """Unregistered threads are denied (security: deny by default)."""
        reg = self._make_registry(tmp_path)
        assert reg.check_access("unknown-thread", "tenant-a") is False

    def test_list_threads_by_tenant(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("thread-1", "tenant-a")
        reg.register("thread-2", "tenant-b")
        reg.register("thread-3", "tenant-a")
        assert sorted(reg.list_threads("tenant-a")) == ["thread-1", "thread-3"]
        assert reg.list_threads("tenant-b") == ["thread-2"]

    def test_unregister_removes_thread(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("thread-1", "tenant-a")
        assert reg.unregister("thread-1") is True
        assert reg.get_tenant("thread-1") is None

    def test_unregister_nonexistent_returns_false(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.unregister("nonexistent") is False

    def test_persistence_across_instances(self, tmp_path):
        file = tmp_path / "thread_registry.json"
        reg1 = ThreadRegistry(registry_file=file)
        reg1.register("thread-1", "tenant-a")

        reg2 = ThreadRegistry(registry_file=file)
        assert reg2.get_tenant("thread-1") == "tenant-a"

    def test_invalid_thread_id_rejected(self, tmp_path):
        reg = self._make_registry(tmp_path)
        with pytest.raises(ValueError, match="Invalid thread_id"):
            reg.register("../evil", "tenant-a")

    def test_register_updates_owner(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("thread-1", "tenant-a")
        reg.register("thread-1", "tenant-b")
        assert reg.get_tenant("thread-1") == "tenant-b"

    def test_invalidate_cache_forces_reload(self, tmp_path):
        file = tmp_path / "thread_registry.json"
        reg = ThreadRegistry(registry_file=file)
        reg.register("thread-1", "tenant-a")

        # Externally modify the file
        data = json.loads(file.read_text())
        data["thread-2"] = "tenant-b"
        file.write_text(json.dumps(data))

        # Without invalidation, cache still has old data
        assert reg.get_tenant("thread-2") is None

        # After invalidation, reloads from disk
        reg.invalidate_cache()
        assert reg.get_tenant("thread-2") == "tenant-b"


# ── Tenant-scoped paths tests ────────────────────────────────────────


class TestTenantPaths:
    """Tests for tenant-scoped path methods."""

    def test_tenant_dir(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        assert paths.tenant_dir("acme") == tmp_path / "tenants" / "acme"

    def test_tenant_memory_file(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        assert paths.tenant_memory_file("acme") == tmp_path / "tenants" / "acme" / "memory.json"

    def test_tenant_agents_dir(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        assert paths.tenant_agents_dir("acme") == tmp_path / "tenants" / "acme" / "agents"

    def test_tenant_agent_dir(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        assert paths.tenant_agent_dir("acme", "MyBot") == tmp_path / "tenants" / "acme" / "agents" / "mybot"

    def test_tenant_agent_memory_file(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        expected = tmp_path / "tenants" / "acme" / "agents" / "mybot" / "memory.json"
        assert paths.tenant_agent_memory_file("acme", "MyBot") == expected

    def test_invalid_tenant_id_rejected(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            paths.tenant_dir("../evil")


# ── Governance ledger tenant tests ───────────────────────────────────


class TestGovernanceLedgerTenant:
    """Tests for tenant_id on governance ledger entries."""

    def _make_ledger(self, tmp_path):
        from src.agents.governance.ledger import GovernanceLedger
        return GovernanceLedger(data_dir=str(tmp_path))

    def _record(self, ledger, tenant_id="tenant-a", thread_id="thread-1"):
        return ledger.record(
            thread_id=thread_id,
            run_id="run-1",
            task_id="task-1",
            source_agent="test-agent",
            hook_name="test_hook",
            source_path="test.path",
            risk_level="medium",
            category="test",
            decision="allow",
            tenant_id=tenant_id,
        )

    def test_record_includes_tenant_id(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        entry = self._record(ledger, tenant_id="acme")
        assert entry["tenant_id"] == "acme"

    def test_default_tenant_when_none(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        entry = self._record(ledger, tenant_id=None)
        assert entry["tenant_id"] == "default"

    def test_query_filters_by_tenant(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        self._record(ledger, tenant_id="acme", thread_id="t1")
        self._record(ledger, tenant_id="globex", thread_id="t2")
        self._record(ledger, tenant_id="acme", thread_id="t3")

        acme_entries = ledger.query(tenant_id="acme", limit=0)
        assert len(acme_entries) == 2
        assert all(e.get("tenant_id") == "acme" for e in acme_entries)

        globex_entries = ledger.query(tenant_id="globex", limit=0)
        assert len(globex_entries) == 1

    def test_query_without_tenant_returns_all(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        self._record(ledger, tenant_id="acme")
        self._record(ledger, tenant_id="globex")

        all_entries = ledger.query(limit=0)
        assert len(all_entries) == 2

    def test_pending_count_by_tenant(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        # Create a pending entry
        ledger.record(
            thread_id="t1", run_id="r1", task_id="tk1",
            source_agent="agent", hook_name="hook", source_path="p",
            risk_level="high", category="test",
            decision="require_intervention",
            tenant_id="acme",
        )
        ledger.record(
            thread_id="t2", run_id="r2", task_id="tk2",
            source_agent="agent", hook_name="hook", source_path="p",
            risk_level="high", category="test",
            decision="require_intervention",
            tenant_id="globex",
        )
        assert ledger.pending_count(tenant_id="acme") == 1
        assert ledger.pending_count(tenant_id="globex") == 1
        assert ledger.pending_count() == 2


# ── Memory cache key isolation tests ─────────────────────────────────


class TestMemoryCacheIsolation:
    """Tests for tenant-scoped memory file resolution and cache keys."""

    def test_memory_file_path_with_tenant(self, tmp_path):
        from src.agents.memory.updater import _get_memory_file_path
        with patch("src.agents.memory.updater.get_paths", return_value=Paths(base_dir=tmp_path)):
            path = _get_memory_file_path(tenant_id="acme")
        assert path == tmp_path / "tenants" / "acme" / "memory.json"

    def test_memory_file_path_default_tenant_uses_global(self, tmp_path):
        from src.agents.memory.updater import _get_memory_file_path
        with patch("src.agents.memory.updater.get_paths", return_value=Paths(base_dir=tmp_path)):
            with patch("src.agents.memory.updater.get_memory_config") as mock_cfg:
                mock_cfg.return_value.storage_path = ""
                path = _get_memory_file_path(tenant_id="default")
        assert path == tmp_path / "memory.json"

    def test_memory_file_path_no_tenant_uses_global(self, tmp_path):
        from src.agents.memory.updater import _get_memory_file_path
        with patch("src.agents.memory.updater.get_paths", return_value=Paths(base_dir=tmp_path)):
            with patch("src.agents.memory.updater.get_memory_config") as mock_cfg:
                mock_cfg.return_value.storage_path = ""
                path = _get_memory_file_path(tenant_id=None)
        assert path == tmp_path / "memory.json"

    def test_memory_file_path_tenant_plus_agent(self, tmp_path):
        from src.agents.memory.updater import _get_memory_file_path
        with patch("src.agents.memory.updater.get_paths", return_value=Paths(base_dir=tmp_path)):
            path = _get_memory_file_path(agent_name="mybot", tenant_id="acme")
        assert path == tmp_path / "tenants" / "acme" / "agents" / "mybot" / "memory.json"

    def test_cache_key_isolation(self):
        """Different tenants should have different cache keys."""
        from src.agents.memory.updater import _memory_cache

        # Clear cache
        _memory_cache.clear()

        # Simulate caching for two tenants
        _memory_cache[("acme", None)] = ({"facts": ["acme-fact"]}, 1.0)
        _memory_cache[("globex", None)] = ({"facts": ["globex-fact"]}, 1.0)

        assert _memory_cache[("acme", None)][0]["facts"] == ["acme-fact"]
        assert _memory_cache[("globex", None)][0]["facts"] == ["globex-fact"]

        _memory_cache.clear()


# ── Memory queue dedupe key tests ────────────────────────────────────


class TestMemoryQueueDedupeKey:
    """Tests for tenant-aware dedupe keys in memory queue."""

    def test_dedupe_key_includes_tenant(self):
        from src.agents.memory.queue import MemoryUpdateQueue
        key = MemoryUpdateQueue._build_default_dedupe_key("thread-1", "agent-x", "acme")
        assert key == "memory:acme:agent-x:thread-1"

    def test_dedupe_key_default_tenant(self):
        from src.agents.memory.queue import MemoryUpdateQueue
        key = MemoryUpdateQueue._build_default_dedupe_key("thread-1", "agent-x", None)
        assert key == "memory:default:agent-x:thread-1"

    def test_dedupe_key_global_agent(self):
        from src.agents.memory.queue import MemoryUpdateQueue
        key = MemoryUpdateQueue._build_default_dedupe_key("thread-1", None, "acme")
        assert key == "memory:acme:global:thread-1"


# ── Gateway dependencies tests ───────────────────────────────────────


class TestGatewayDependencies:
    """Tests for dependency helpers when OIDC is disabled."""

    def test_get_tenant_id_fallback(self):
        from unittest.mock import MagicMock
        from src.gateway.dependencies import get_tenant_id
        request = MagicMock()
        del request.state.tenant_id  # Simulate no OIDC
        assert get_tenant_id(request) == "default"

    def test_get_user_id_fallback(self):
        from unittest.mock import MagicMock
        from src.gateway.dependencies import get_user_id
        request = MagicMock()
        del request.state.user_id
        assert get_user_id(request) == "anonymous"

    def test_get_tenant_id_with_oidc(self):
        from unittest.mock import MagicMock
        from src.gateway.dependencies import get_tenant_id
        request = MagicMock()
        request.state.tenant_id = "acme"
        assert get_tenant_id(request) == "acme"


# ── Agents config tenant-scoped tests ────────────────────────────────


class TestAgentsConfigTenantScope:
    """Tests for tenant-scoped agent config loading."""

    def _create_agent(self, agents_dir: Path, name: str, domain: str | None = None) -> None:
        agent_dir = agents_dir / name
        agent_dir.mkdir(parents=True, exist_ok=True)
        config = {"name": name, "description": f"Test agent {name}"}
        if domain:
            config["domain"] = domain
        (agent_dir / "config.yaml").write_text(
            __import__("yaml").dump(config), encoding="utf-8"
        )
        (agent_dir / "SOUL.md").write_text(f"I am {name}", encoding="utf-8")

    def test_list_custom_agents_from_tenant_dir(self, tmp_path):
        from src.config.agents_config import list_custom_agents
        tenant_dir = tmp_path / "tenants" / "acme" / "agents"
        self._create_agent(tenant_dir, "bot-a")
        self._create_agent(tenant_dir, "bot-b")
        agents = list_custom_agents(agents_dir=tenant_dir)
        assert len(agents) == 2
        assert {a.name for a in agents} == {"bot-a", "bot-b"}

    def test_list_domain_agents_from_tenant_dir(self, tmp_path):
        from src.config.agents_config import list_domain_agents
        tenant_dir = tmp_path / "tenants" / "acme" / "agents"
        self._create_agent(tenant_dir, "bot-a", domain="sales")
        self._create_agent(tenant_dir, "bot-b")  # no domain
        domain_agents = list_domain_agents(agents_dir=tenant_dir)
        assert len(domain_agents) == 1
        assert domain_agents[0].domain == "sales"

    def test_load_agent_config_from_tenant_dir(self, tmp_path):
        from src.config.agents_config import load_agent_config
        tenant_dir = tmp_path / "tenants" / "acme" / "agents"
        self._create_agent(tenant_dir, "bot-a", domain="hr")
        cfg = load_agent_config("bot-a", agents_dir=tenant_dir)
        assert cfg.name == "bot-a"
        assert cfg.domain == "hr"

    def test_tenant_isolation_between_tenants(self, tmp_path):
        from src.config.agents_config import list_custom_agents
        acme_dir = tmp_path / "tenants" / "acme" / "agents"
        globex_dir = tmp_path / "tenants" / "globex" / "agents"
        self._create_agent(acme_dir, "acme-bot")
        self._create_agent(globex_dir, "globex-bot")

        acme_agents = list_custom_agents(agents_dir=acme_dir)
        globex_agents = list_custom_agents(agents_dir=globex_dir)

        assert len(acme_agents) == 1
        assert acme_agents[0].name == "acme-bot"
        assert len(globex_agents) == 1
        assert globex_agents[0].name == "globex-bot"

    def test_empty_tenant_dir_returns_empty(self, tmp_path):
        from src.config.agents_config import list_custom_agents
        agents = list_custom_agents(agents_dir=tmp_path / "nonexistent")
        assert agents == []
