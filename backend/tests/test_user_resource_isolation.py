"""Tests for user-level personal resource isolation (Phases 0–4).

Covers:
- Phase 1: Path methods, layered agent loading, skill 3-layer merge, ExtensionsConfig user overlay
- Phase 2: MCP scope keys, composite cache keys
- Phase 3: /api/me/* router guards, lifecycle cleanup
- Phase 4: Promotion store submit/resolve
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.config.paths import Paths


@pytest.fixture
def paths(tmp_path):
    return Paths(base_dir=tmp_path)


# ══════════════════════════════════════════════════════════════════════
#  Phase 1 — Path methods
# ══════════════════════════════════════════════════════════════════════


class TestUserPathMethods:
    def test_tenant_user_agents_dir(self, paths, tmp_path):
        p = paths.tenant_user_agents_dir("acme", "alice")
        assert p == tmp_path / "tenants" / "acme" / "users" / "alice" / "agents"

    def test_tenant_user_agent_dir(self, paths, tmp_path):
        p = paths.tenant_user_agent_dir("acme", "alice", "MyAgent")
        assert p == tmp_path / "tenants" / "acme" / "users" / "alice" / "agents" / "myagent"

    def test_tenant_user_skills_dir(self, paths, tmp_path):
        p = paths.tenant_user_skills_dir("acme", "alice")
        assert p == tmp_path / "tenants" / "acme" / "users" / "alice" / "skills"

    def test_tenant_user_extensions_config(self, paths, tmp_path):
        p = paths.tenant_user_extensions_config("acme", "alice")
        assert p == tmp_path / "tenants" / "acme" / "users" / "alice" / "extensions_config.json"


class TestResolveUserAgentsDir:
    def test_returns_none_for_default_tenant(self):
        from src.config.paths import resolve_tenant_user_agents_dir
        assert resolve_tenant_user_agents_dir("default", "alice") is None

    def test_returns_none_for_anonymous_user(self):
        from src.config.paths import resolve_tenant_user_agents_dir
        assert resolve_tenant_user_agents_dir("acme", "anonymous") is None

    def test_returns_path_for_real_user(self):
        from src.config.paths import resolve_tenant_user_agents_dir
        result = resolve_tenant_user_agents_dir("acme", "alice")
        assert result is not None
        assert "acme" in str(result)
        assert "alice" in str(result)
        assert str(result).endswith("agents")


# ══════════════════════════════════════════════════════════════════════
#  Phase 1 — Layered agent loading
# ══════════════════════════════════════════════════════════════════════


def _create_agent(agents_dir: Path, name: str, description: str = "test") -> None:
    agent_dir = agents_dir / name.lower()
    agent_dir.mkdir(parents=True, exist_ok=True)
    config = {"name": name.lower(), "description": description}
    (agent_dir / "config.yaml").write_text(yaml.dump(config), encoding="utf-8")
    (agent_dir / "SOUL.md").write_text("Test soul", encoding="utf-8")


def _patch_agent_paths(paths):
    """Context manager that patches all get_paths references used by agents_config."""
    return patch.multiple(
        "src.config.agents_config",
        get_paths=MagicMock(return_value=paths),
        resolve_tenant_agents_dir=lambda tid: paths.tenant_agents_dir(tid) if tid and tid != "default" else None,
        resolve_tenant_user_agents_dir=lambda tid, uid: (
            paths.tenant_user_agents_dir(tid, uid)
            if tid and tid != "default" and uid and uid != "anonymous"
            else None
        ),
    )


class TestLayeredAgentLoading:
    def test_user_overrides_tenant(self, paths, tmp_path):
        """Personal agent with same name should shadow tenant agent."""
        from src.config.agents_config import load_agent_config_layered

        tenant_dir = paths.tenant_agents_dir("acme")
        user_dir = paths.tenant_user_agents_dir("acme", "alice")
        _create_agent(tenant_dir, "helper", description="tenant version")
        _create_agent(user_dir, "helper", description="personal version")

        with _patch_agent_paths(paths):
            cfg = load_agent_config_layered("helper", tenant_id="acme", user_id="alice")

        assert cfg is not None
        assert cfg.description == "personal version"
        assert cfg.source == "personal"

    def test_tenant_overrides_platform(self, paths, tmp_path):
        """Tenant agent should shadow platform agent."""
        from src.config.agents_config import load_agent_config_layered

        platform_dir = paths.agents_dir
        tenant_dir = paths.tenant_agents_dir("acme")
        _create_agent(platform_dir, "shared", description="platform version")
        _create_agent(tenant_dir, "shared", description="tenant version")

        with _patch_agent_paths(paths):
            cfg = load_agent_config_layered("shared", tenant_id="acme", user_id="alice")

        assert cfg is not None
        assert cfg.description == "tenant version"
        assert cfg.source == "tenant"

    def test_falls_through_to_platform(self, paths, tmp_path):
        """Platform agent returned when no tenant/user override exists."""
        from src.config.agents_config import load_agent_config_layered

        _create_agent(paths.agents_dir, "base-agent", description="platform only")

        with _patch_agent_paths(paths):
            cfg = load_agent_config_layered("base-agent", tenant_id="acme", user_id="alice")

        assert cfg is not None
        assert cfg.description == "platform only"
        assert cfg.source == "platform"

    def test_returns_none_for_missing(self, paths, tmp_path):
        """Returns None when agent not found in any layer."""
        from src.config.agents_config import load_agent_config_layered

        with _patch_agent_paths(paths):
            cfg = load_agent_config_layered("nonexistent", tenant_id="acme", user_id="alice")

        assert cfg is None


class TestListAllAgents:
    def test_merges_three_layers(self, paths, tmp_path):
        """list_all_agents should merge all three layers with correct source."""
        from src.config.agents_config import list_all_agents

        _create_agent(paths.agents_dir, "platform-only", description="platform")
        _create_agent(paths.tenant_agents_dir("acme"), "tenant-only", description="tenant")
        _create_agent(paths.tenant_user_agents_dir("acme", "alice"), "personal-only", description="personal")
        # Shadow: personal overrides tenant
        _create_agent(paths.tenant_agents_dir("acme"), "shared", description="tenant-shared")
        _create_agent(paths.tenant_user_agents_dir("acme", "alice"), "shared", description="personal-shared")

        with _patch_agent_paths(paths):
            agents = list_all_agents(tenant_id="acme", user_id="alice")

        names = {a.name for a in agents}
        assert "platform-only" in names
        assert "tenant-only" in names
        assert "personal-only" in names
        assert "shared" in names

        by_name = {a.name: a for a in agents}
        assert by_name["platform-only"].source == "platform"
        assert by_name["tenant-only"].source == "tenant"
        assert by_name["personal-only"].source == "personal"
        assert by_name["shared"].source == "personal"  # personal shadows tenant


# ══════════════════════════════════════════════════════════════════════
#  Phase 1 — Skill source field
# ══════════════════════════════════════════════════════════════════════


class TestSkillSourceField:
    def test_skill_has_source_field(self):
        from src.skills.types import Skill
        s = Skill(name="test", description="d", license=None, skill_dir=Path("/tmp"), skill_file=Path("/tmp/SKILL.md"), relative_path=Path("."), category="public")
        assert s.source == "platform"

    def test_skill_accepts_personal_source(self):
        from src.skills.types import Skill
        s = Skill(name="test", description="d", license=None, skill_dir=Path("/tmp"), skill_file=Path("/tmp/SKILL.md"), relative_path=Path("."), category="personal", source="personal")
        assert s.source == "personal"


# ══════════════════════════════════════════════════════════════════════
#  Phase 1 — ExtensionsConfig.from_user
# ══════════════════════════════════════════════════════════════════════


class TestExtensionsConfigFromUser:
    def test_falls_back_to_tenant_for_anonymous(self):
        """Anonymous user should return same as from_tenant."""
        from src.config.extensions_config import ExtensionsConfig as EC
        sentinel = MagicMock()
        with patch.object(EC, "from_tenant", return_value=sentinel) as mock_tenant:
            result = EC.from_user("acme", "anonymous")
            mock_tenant.assert_called_once_with("acme")
            assert result is sentinel

    def test_falls_back_for_default_tenant(self):
        """Default tenant should return same as from_tenant."""
        from src.config.extensions_config import ExtensionsConfig as EC
        sentinel = MagicMock()
        with patch.object(EC, "from_tenant", return_value=sentinel) as mock_tenant:
            result = EC.from_user("default", "alice")
            mock_tenant.assert_called_once_with("default")
            assert result is sentinel


# ══════════════════════════════════════════════════════════════════════
#  Phase 2 — MCP scope keys
# ══════════════════════════════════════════════════════════════════════


class TestMcpScopeKeys:
    def test_user_scope_key(self):
        from src.mcp.runtime_manager import McpRuntimeManager
        key = McpRuntimeManager.scope_key_for_user("acme", "alice")
        assert key == "tenant:acme:user:alice:global"

    def test_user_scope_key_falls_back_for_anonymous(self):
        from src.mcp.runtime_manager import McpRuntimeManager
        key = McpRuntimeManager.scope_key_for_user("acme", "anonymous")
        tenant_key = McpRuntimeManager.scope_key_for_tenant("acme")
        assert key == tenant_key

    def test_user_scope_key_falls_back_for_default(self):
        from src.mcp.runtime_manager import McpRuntimeManager
        key = McpRuntimeManager.scope_key_for_user("default", "alice")
        tenant_key = McpRuntimeManager.scope_key_for_tenant("default")
        assert key == tenant_key

    def test_user_agent_scope_key(self):
        from src.mcp.runtime_manager import McpRuntimeManager
        key = McpRuntimeManager.scope_key_for_user_agent("helper", "acme", "alice")
        assert key == "tenant:acme:user:alice:domain:helper"

    def test_user_agent_scope_key_falls_back(self):
        from src.mcp.runtime_manager import McpRuntimeManager
        key = McpRuntimeManager.scope_key_for_user_agent("helper", "acme", "anonymous")
        tenant_key = McpRuntimeManager.scope_key_for_agent("helper", "acme")
        assert key == tenant_key


# ══════════════════════════════════════════════════════════════════════
#  Phase 2 — MCP cache composite key
# ══════════════════════════════════════════════════════════════════════


class TestMcpCacheKey:
    def test_default_cache_key(self):
        from src.mcp.cache import _cache_key
        assert _cache_key(None) == "default:anonymous"
        assert _cache_key("default") == "default:anonymous"

    def test_tenant_cache_key(self):
        from src.mcp.cache import _cache_key
        # Without a user extensions file, falls back to tenant key
        key = _cache_key("acme", "alice")
        assert key.startswith("acme:")


# ══════════════════════════════════════════════════════════════════════
#  Phase 3 — /api/me guard
# ══════════════════════════════════════════════════════════════════════


class TestRequireIdentifiedUser:
    def test_rejects_default_tenant(self):
        from src.gateway.routers.me import _require_identified_user
        with pytest.raises(Exception) as exc_info:
            _require_identified_user("default", "alice")
        assert exc_info.value.status_code == 403

    def test_rejects_anonymous_user(self):
        from src.gateway.routers.me import _require_identified_user
        with pytest.raises(Exception) as exc_info:
            _require_identified_user("acme", "anonymous")
        assert exc_info.value.status_code == 403

    def test_accepts_identified_user(self):
        from src.gateway.routers.me import _require_identified_user
        tenant_id, user_id = _require_identified_user("acme", "alice")
        assert tenant_id == "acme"
        assert user_id == "alice"


# ══════════════════════════════════════════════════════════════════════
#  Phase 3 — Path traversal protection
# ══════════════════════════════════════════════════════════════════════


class TestPathTraversalProtection:
    def test_rejects_dot_dot(self):
        from src.gateway.routers.me import _validate_path_safe
        with pytest.raises(Exception) as exc_info:
            _validate_path_safe("../escape", "name")
        assert exc_info.value.status_code == 400

    def test_rejects_forward_slash(self):
        from src.gateway.routers.me import _validate_path_safe
        with pytest.raises(Exception) as exc_info:
            _validate_path_safe("a/b", "name")
        assert exc_info.value.status_code == 400

    def test_rejects_backslash(self):
        from src.gateway.routers.me import _validate_path_safe
        with pytest.raises(Exception) as exc_info:
            _validate_path_safe("a\\b", "name")
        assert exc_info.value.status_code == 400

    def test_allows_clean_name(self):
        from src.gateway.routers.me import _validate_path_safe
        _validate_path_safe("my-agent-1", "name")  # Should not raise


# ══════════════════════════════════════════════════════════════════════
#  Phase 4 — Promotion store
# ══════════════════════════════════════════════════════════════════════


class TestPromotionStore:
    def test_submit_and_list(self, paths, tmp_path):
        from src.promotion.store import PromotionStore

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            req = store.submit("acme", "alice", "agent", "my-helper")
            assert req["status"] == "pending"
            assert req["resource_name"] == "my-helper"
            assert req["target_name"] == "my-helper"

            pending = store.list_pending("acme")
            assert len(pending) == 1
            assert pending[0]["request_id"] == req["request_id"]

    def test_duplicate_pending_rejected(self, paths, tmp_path):
        from src.promotion.store import PromotionStore

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            store.submit("acme", "alice", "agent", "dup-agent")
            with pytest.raises(ValueError, match="already exists"):
                store.submit("acme", "alice", "agent", "dup-agent")

    def test_resolve_approve(self, paths, tmp_path):
        from src.promotion.store import PromotionStore
        from src.promotion.types import PromotionStatus

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            req = store.submit("acme", "alice", "skill", "my-skill")
            updated = store.resolve("acme", req["request_id"], PromotionStatus.APPROVED, "admin-bob", "looks good")

            assert updated["status"] == "approved"
            assert updated["resolved_by"] == "admin-bob"
            assert updated["reason"] == "looks good"
            assert updated["resolved_at"] is not None

    def test_resolve_reject(self, paths, tmp_path):
        from src.promotion.store import PromotionStore
        from src.promotion.types import PromotionStatus

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            req = store.submit("acme", "bob", "agent", "bad-agent")
            updated = store.resolve("acme", req["request_id"], PromotionStatus.REJECTED, "admin-carol", "not ready")

            assert updated["status"] == "rejected"

    def test_cannot_resolve_twice(self, paths, tmp_path):
        from src.promotion.store import PromotionStore
        from src.promotion.types import PromotionStatus

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            req = store.submit("acme", "alice", "agent", "resolved-agent")
            store.resolve("acme", req["request_id"], PromotionStatus.APPROVED, "admin")
            with pytest.raises(ValueError, match="already resolved"):
                store.resolve("acme", req["request_id"], PromotionStatus.REJECTED, "admin2")

    def test_list_by_user(self, paths, tmp_path):
        from src.promotion.store import PromotionStore

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            store.submit("acme", "alice", "agent", "a1")
            store.submit("acme", "bob", "agent", "b1")
            store.submit("acme", "alice", "skill", "s1")

            alice_reqs = store.list_by_user("acme", "alice")
            assert len(alice_reqs) == 2
            bob_reqs = store.list_by_user("acme", "bob")
            assert len(bob_reqs) == 1

    def test_resolve_nonexistent(self, paths, tmp_path):
        from src.promotion.store import PromotionStore
        from src.promotion.types import PromotionStatus

        store = PromotionStore()
        with patch("src.promotion.store.get_paths", return_value=paths):
            with pytest.raises(ValueError, match="not found"):
                store.resolve("acme", "nonexistent-id", PromotionStatus.APPROVED, "admin")


# ══════════════════════════════════════════════════════════════════════
#  Phase 2 — AgentConfig.source field
# ══════════════════════════════════════════════════════════════════════


class TestAgentConfigSourceField:
    def test_source_field_defaults_none(self):
        from src.config.agents_config import AgentConfig
        cfg = AgentConfig(name="test", description="d")
        assert cfg.source is None

    def test_source_field_excluded_from_dict(self):
        from src.config.agents_config import AgentConfig
        cfg = AgentConfig(name="test", description="d", source="personal")
        dumped = cfg.model_dump()
        assert "source" not in dumped
