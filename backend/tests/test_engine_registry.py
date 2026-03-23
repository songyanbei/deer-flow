"""Tests for Engine Registry Phase 1: registry, builders, CRUD integration."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from src.agents.lead_agent.engine_registry import (
    EngineRegistry,
    engine_registry,
    get_engine_builder,
    list_supported_engine_types,
    normalize_engine_type,
)
from src.agents.lead_agent.engines import (
    BaseEngineBuilder,
    DefaultEngineBuilder,
    EnginePromptKwargs,
    EngineRuntimeOptions,
    ReactEngineBuilder,
    ReadOnlyExplorerEngineBuilder,
    SopEngineBuilder,
)


# ===========================================================================
# 1. Registry Layer
# ===========================================================================


class TestRegistryCanonicalResolution:
    """Canonical values resolve directly to the correct builder."""

    @pytest.mark.parametrize("canonical", ["default", "react", "read_only_explorer", "sop"])
    def test_canonical_resolves_to_builder(self, canonical):
        builder = get_engine_builder(canonical)
        assert builder.canonical_name == canonical

    def test_none_resolves_to_default(self):
        builder = get_engine_builder(None)
        assert builder.canonical_name == "default"

    def test_empty_string_resolves_to_default(self):
        builder = get_engine_builder("")
        assert builder.canonical_name == "default"

    def test_whitespace_resolves_to_default(self):
        builder = get_engine_builder("   ")
        assert builder.canonical_name == "default"


class TestRegistryAliasResolution:
    """Alias values resolve to the correct canonical builder."""

    @pytest.mark.parametrize(
        "alias,expected_canonical",
        [
            ("ReAct", "react"),
            ("REACT", "react"),
            ("ReadOnly_Explorer", "read_only_explorer"),
            ("readonly", "read_only_explorer"),
            ("readonly_explorer", "read_only_explorer"),
            ("SOP", "sop"),
            ("sop_engine", "sop"),
        ],
    )
    def test_alias_resolves(self, alias, expected_canonical):
        builder = get_engine_builder(alias)
        assert builder.canonical_name == expected_canonical

    @pytest.mark.parametrize(
        "alias,expected_canonical",
        [
            ("ReAct", "react"),
            ("readonly", "read_only_explorer"),
            ("SOP", "sop"),
            ("sop_engine", "sop"),
        ],
    )
    def test_normalize_alias(self, alias, expected_canonical):
        assert normalize_engine_type(alias) == expected_canonical


class TestRegistryUnknownFallback:
    """Unknown engine types fall back to default with a warning."""

    def test_unknown_falls_back_to_default(self, caplog):
        with caplog.at_level(logging.WARNING):
            builder = get_engine_builder("nonexistent_engine")
        assert builder.canonical_name == "default"
        assert "Unknown engine_type" in caplog.text

    def test_normalize_unknown_returns_default(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = normalize_engine_type("bogus")
        assert result == "default"

    def test_normalize_none_returns_none(self):
        assert normalize_engine_type(None) is None

    def test_normalize_empty_returns_none(self):
        assert normalize_engine_type("") is None


class TestRegistryListSupported:
    """list_supported_engine_types returns all canonical names."""

    def test_lists_all_engines(self):
        supported = list_supported_engine_types()
        assert set(supported) == {"default", "react", "read_only_explorer", "sop"}

    def test_list_is_sorted(self):
        supported = list_supported_engine_types()
        assert supported == sorted(supported)


# ===========================================================================
# 2. Builder Layer
# ===========================================================================


class TestDefaultBuilder:
    def test_canonical_name(self):
        b = DefaultEngineBuilder()
        assert b.canonical_name == "default"

    def test_prompt_kwargs(self):
        b = DefaultEngineBuilder()
        kwargs = b.build_prompt_kwargs()
        assert kwargs.engine_mode == "default"

    def test_no_tool_filtering(self):
        b = DefaultEngineBuilder()
        tools = [SimpleNamespace(name="write_file"), SimpleNamespace(name="read_file")]
        result = b.prepare_extra_tools(tools)
        assert len(result) == 2

    def test_runtime_options(self):
        b = DefaultEngineBuilder()
        opts = b.prepare_runtime_options()
        assert opts.filter_read_only_tools is False


class TestReactBuilder:
    def test_canonical_name(self):
        b = ReactEngineBuilder()
        assert b.canonical_name == "react"

    def test_aliases(self):
        b = ReactEngineBuilder()
        assert "ReAct" in b.aliases

    def test_prompt_kwargs(self):
        b = ReactEngineBuilder()
        kwargs = b.build_prompt_kwargs()
        assert kwargs.engine_mode == "react"


class TestReadOnlyExplorerBuilder:
    def test_canonical_name(self):
        b = ReadOnlyExplorerEngineBuilder()
        assert b.canonical_name == "read_only_explorer"

    def test_aliases(self):
        b = ReadOnlyExplorerEngineBuilder()
        assert "readonly" in b.aliases
        assert "ReadOnly_Explorer" in b.aliases

    def test_prompt_kwargs(self):
        b = ReadOnlyExplorerEngineBuilder()
        kwargs = b.build_prompt_kwargs()
        assert kwargs.engine_mode == "read_only_explorer"

    def test_filters_write_tools(self):
        b = ReadOnlyExplorerEngineBuilder()
        tools = [
            SimpleNamespace(name="get_contacts"),
            SimpleNamespace(name="create_contact"),
            SimpleNamespace(name="delete_contact"),
        ]
        result = b.prepare_extra_tools(tools)
        names = [t.name for t in result]
        assert "get_contacts" in names
        assert "create_contact" not in names
        assert "delete_contact" not in names

    def test_runtime_options(self):
        b = ReadOnlyExplorerEngineBuilder()
        opts = b.prepare_runtime_options()
        assert opts.filter_read_only_tools is True


class TestSopBuilder:
    def test_canonical_name(self):
        b = SopEngineBuilder()
        assert b.canonical_name == "sop"

    def test_aliases(self):
        b = SopEngineBuilder()
        assert "SOP" in b.aliases
        assert "sop_engine" in b.aliases

    def test_prompt_kwargs(self):
        b = SopEngineBuilder()
        kwargs = b.build_prompt_kwargs()
        assert kwargs.engine_mode == "sop"


# ===========================================================================
# 3. Registry Object (EngineRegistry class)
# ===========================================================================


class TestEngineRegistryClass:
    def test_register_and_retrieve(self):
        reg = EngineRegistry()
        builder = DefaultEngineBuilder()
        reg.register(builder)
        assert reg.get_engine_builder("default").canonical_name == "default"

    def test_alias_registration(self):
        reg = EngineRegistry()
        reg.register(ReactEngineBuilder())
        reg.register(DefaultEngineBuilder())
        assert reg.get_engine_builder("ReAct").canonical_name == "react"

    def test_list_supported(self):
        reg = EngineRegistry()
        reg.register(DefaultEngineBuilder())
        reg.register(SopEngineBuilder())
        assert set(reg.list_supported_engine_types()) == {"default", "sop"}


# ===========================================================================
# 4. CRUD Layer
# ===========================================================================


def _make_paths(base_dir: Path):
    from src.config.paths import Paths
    return Paths(base_dir=base_dir)


def _write_agent(base_dir: Path, name: str, config: dict, soul: str = "You are helpful."):
    agent_dir = base_dir / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name
    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)
    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


from contextlib import contextmanager


@contextmanager
def _multi_patch(paths):
    """Patch get_paths in both routers and config modules."""
    with patch("src.gateway.routers.agents.get_paths", return_value=paths), \
         patch("src.config.agents_config.get_paths", return_value=paths):
        yield


class TestCrudEngineType:
    """Agent CRUD endpoints handle engine_type correctly."""

    def _patch_paths(self, tmp_path):
        """Return a context manager that patches get_paths in both routers and config."""
        paths = _make_paths(tmp_path)
        return _multi_patch(paths)

    def test_create_agent_with_engine_type(self, tmp_path):
        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.post("/api/agents", json={
                "name": "test-agent",
                "description": "test",
                "engine_type": "react",
                "soul": "You are helpful.",
            })
            assert resp.status_code == 201
            data = resp.json()
            assert data["engine_type"] == "react"

    def test_create_agent_with_alias_normalizes(self, tmp_path):
        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.post("/api/agents", json={
                "name": "test-alias",
                "description": "test",
                "engine_type": "ReAct",
                "soul": "You are helpful.",
            })
            assert resp.status_code == 201
            data = resp.json()
            assert data["engine_type"] == "react"

    def test_create_agent_without_engine_type(self, tmp_path):
        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.post("/api/agents", json={
                "name": "test-no-engine",
                "description": "test",
                "soul": "You are helpful.",
            })
            assert resp.status_code == 201
            data = resp.json()
            assert data["engine_type"] is None

    def test_get_agent_returns_engine_type(self, tmp_path):
        _write_agent(tmp_path, "my-agent", {"description": "d", "engine_type": "sop"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.get("/api/agents/my-agent")
            assert resp.status_code == 200
            assert resp.json()["engine_type"] == "sop"

    def test_update_agent_engine_type(self, tmp_path):
        _write_agent(tmp_path, "upd-agent", {"description": "d", "engine_type": "react"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.put("/api/agents/upd-agent", json={"engine_type": "sop"})
            assert resp.status_code == 200
            assert resp.json()["engine_type"] == "sop"

    def test_update_agent_engine_type_alias_normalizes(self, tmp_path):
        _write_agent(tmp_path, "alias-agent", {"description": "d"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.put("/api/agents/alias-agent", json={"engine_type": "ReadOnly_Explorer"})
            assert resp.status_code == 200
            assert resp.json()["engine_type"] == "read_only_explorer"

    def test_list_agents_includes_engine_type(self, tmp_path):
        _write_agent(tmp_path, "list-agent", {"description": "d", "engine_type": "react"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.get("/api/agents")
            assert resp.status_code == 200
            agents = resp.json()["agents"]
            found = [a for a in agents if a["name"] == "list-agent"]
            assert len(found) == 1
            assert found[0]["engine_type"] == "react"

    def test_config_yaml_persists_canonical(self, tmp_path):
        """Verify that config.yaml on disk has the canonical value after create."""
        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            client.post("/api/agents", json={
                "name": "persist-test",
                "description": "test",
                "engine_type": "SOP",
                "soul": "test",
            })

        config_file = tmp_path / "agents" / "persist-test" / "config.yaml"
        with open(config_file) as f:
            data = yaml.safe_load(f)
        assert data["engine_type"] == "sop"


# ===========================================================================
# 5. Runtime Build Layer (prompt integration)
# ===========================================================================


class TestRuntimePromptIntegration:
    """Engine builders produce correct prompt kwargs for apply_prompt_template."""

    def test_default_engine_mode(self):
        builder = get_engine_builder("default")
        kwargs = builder.build_prompt_kwargs()
        assert kwargs.engine_mode == "default"

    def test_react_engine_mode(self):
        builder = get_engine_builder("react")
        kwargs = builder.build_prompt_kwargs()
        assert kwargs.engine_mode == "react"

    def test_read_only_explorer_engine_mode(self):
        builder = get_engine_builder("read_only_explorer")
        kwargs = builder.build_prompt_kwargs()
        assert kwargs.engine_mode == "read_only_explorer"

    def test_sop_engine_mode(self):
        builder = get_engine_builder("sop")
        kwargs = builder.build_prompt_kwargs()
        assert kwargs.engine_mode == "sop"

    def test_unknown_engine_uses_default_mode(self):
        builder = get_engine_builder("unknown_thing")
        kwargs = builder.build_prompt_kwargs()
        assert kwargs.engine_mode == "default"


# ===========================================================================
# 6. Additional Registry Edge Cases
# ===========================================================================


class TestRegistryCaseInsensitivity:
    """Canonical names and aliases are resolved case-insensitively."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("DEFAULT", "default"),
            ("Default", "default"),
            ("REACT", "react"),
            ("React", "react"),
            ("READ_ONLY_EXPLORER", "read_only_explorer"),
            ("Read_Only_Explorer", "read_only_explorer"),
            ("Sop", "sop"),
        ],
    )
    def test_case_insensitive_resolution(self, input_val, expected):
        builder = get_engine_builder(input_val)
        assert builder.canonical_name == expected

    def test_normalize_whitespace_padded_input(self):
        assert normalize_engine_type("  react  ") == "react"

    def test_normalize_whitespace_padded_alias(self):
        assert normalize_engine_type("  ReAct  ") == "react"

    def test_normalize_tab_padded_input(self):
        assert normalize_engine_type("\treact\t") == "react"


class TestRegistryMultipleUnknowns:
    """Multiple unknown engine lookups all produce warnings."""

    def test_each_unknown_produces_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            get_engine_builder("engine_alpha")
            get_engine_builder("engine_beta")
        warning_lines = [r for r in caplog.records if "Unknown engine_type" in r.message]
        assert len(warning_lines) == 2
        assert "engine_alpha" in warning_lines[0].message
        assert "engine_beta" in warning_lines[1].message


class TestRegistryEmptyState:
    """EngineRegistry with no registered builders handles edge cases."""

    def test_empty_registry_unknown_falls_back_safely(self, caplog):
        """Empty registry with unknown input: normalize returns 'default' but get_engine_builder may KeyError."""
        reg = EngineRegistry()
        with caplog.at_level(logging.WARNING):
            canonical = reg.normalize_engine_type("anything")
        assert canonical == "default"
        # Without a 'default' builder registered, get_engine_builder should
        # not crash (it returns from .get with fallback)
        # But the fallback self._builders["default"] would KeyError
        # This tests the current behavior:
        with pytest.raises(KeyError):
            reg.get_engine_builder("anything")

    def test_empty_registry_list_supported_empty(self):
        reg = EngineRegistry()
        assert reg.list_supported_engine_types() == []

    def test_empty_registry_none_input(self):
        reg = EngineRegistry()
        assert reg.normalize_engine_type(None) is None


# ===========================================================================
# 7. Additional Builder Edge Cases
# ===========================================================================


class TestReactBuilderToolPassthrough:
    """ReactEngineBuilder does NOT filter tools."""

    def test_no_tool_filtering(self):
        b = ReactEngineBuilder()
        tools = [
            SimpleNamespace(name="write_file"),
            SimpleNamespace(name="read_file"),
            SimpleNamespace(name="delete_record"),
        ]
        result = b.prepare_extra_tools(tools)
        assert len(result) == 3

    def test_runtime_options_no_filter(self):
        b = ReactEngineBuilder()
        opts = b.prepare_runtime_options()
        assert opts.filter_read_only_tools is False


class TestSopBuilderToolPassthrough:
    """SopEngineBuilder does NOT filter tools."""

    def test_no_tool_filtering(self):
        b = SopEngineBuilder()
        tools = [
            SimpleNamespace(name="write_file"),
            SimpleNamespace(name="read_file"),
            SimpleNamespace(name="cancel_order"),
        ]
        result = b.prepare_extra_tools(tools)
        assert len(result) == 3

    def test_runtime_options_no_filter(self):
        b = SopEngineBuilder()
        opts = b.prepare_runtime_options()
        assert opts.filter_read_only_tools is False


class TestReadOnlyExplorerEdgeCases:
    """Additional edge cases for ReadOnlyExplorerEngineBuilder."""

    def test_empty_tool_list(self):
        b = ReadOnlyExplorerEngineBuilder()
        result = b.prepare_extra_tools([])
        assert result == []

    def test_all_read_only_tools_preserved(self):
        b = ReadOnlyExplorerEngineBuilder()
        tools = [
            SimpleNamespace(name="get_info"),
            SimpleNamespace(name="list_items"),
            SimpleNamespace(name="search_records"),
            SimpleNamespace(name="fetch_data"),
        ]
        result = b.prepare_extra_tools(tools)
        assert len(result) == 4

    def test_all_write_tools_removed(self):
        b = ReadOnlyExplorerEngineBuilder()
        tools = [
            SimpleNamespace(name="create_item"),
            SimpleNamespace(name="update_record"),
            SimpleNamespace(name="delete_entry"),
            SimpleNamespace(name="insert_row"),
            SimpleNamespace(name="modify_setting"),
            SimpleNamespace(name="submit_form"),
            SimpleNamespace(name="cancel_order"),
            SimpleNamespace(name="write_data"),
        ]
        result = b.prepare_extra_tools(tools)
        assert len(result) == 0

    def test_mixed_keyword_tool_filtered(self):
        """Tools with write keywords anywhere in the name should be filtered."""
        b = ReadOnlyExplorerEngineBuilder()
        tools = [
            SimpleNamespace(name="bulk_create_contacts"),
            SimpleNamespace(name="read_and_update_entry"),
        ]
        result = b.prepare_extra_tools(tools)
        assert len(result) == 0


class TestDefaultBuilderDefaults:
    """DefaultEngineBuilder has empty aliases."""

    def test_aliases_empty(self):
        b = DefaultEngineBuilder()
        assert b.aliases == []


class TestBuilderReturnTypes:
    """Builders return correct dataclass types."""

    @pytest.mark.parametrize("engine", ["default", "react", "read_only_explorer", "sop"])
    def test_build_prompt_kwargs_returns_engine_prompt_kwargs(self, engine):
        builder = get_engine_builder(engine)
        result = builder.build_prompt_kwargs()
        assert isinstance(result, EnginePromptKwargs)

    @pytest.mark.parametrize("engine", ["default", "react", "read_only_explorer", "sop"])
    def test_prepare_runtime_options_returns_engine_runtime_options(self, engine):
        builder = get_engine_builder(engine)
        result = builder.prepare_runtime_options()
        assert isinstance(result, EngineRuntimeOptions)


# ===========================================================================
# 8. Additional CRUD Edge Cases
# ===========================================================================


class TestCrudEdgeCases:
    """Additional CRUD edge cases for engine_type handling."""

    def _patch_paths(self, tmp_path):
        paths = _make_paths(tmp_path)
        return _multi_patch(paths)

    def test_update_without_engine_type_preserves_existing(self, tmp_path):
        """PUT without engine_type should NOT clear the existing value."""
        _write_agent(tmp_path, "keep-engine", {"description": "d", "engine_type": "react"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            # Update description only, no engine_type
            resp = client.put("/api/agents/keep-engine", json={"description": "updated"})
            assert resp.status_code == 200
            assert resp.json()["engine_type"] == "react"

    def test_create_with_unknown_engine_type_fallback(self, tmp_path):
        """Create agent with unknown engine_type: should persist as 'default' (fallback)."""
        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.post("/api/agents", json={
                "name": "unknown-eng",
                "description": "test",
                "engine_type": "nonexistent_engine",
                "soul": "You are helpful.",
            })
            assert resp.status_code == 201
            data = resp.json()
            # normalize_engine_type returns "default" for unknowns
            assert data["engine_type"] == "default"

    def test_old_agent_without_engine_type_loads_in_list(self, tmp_path):
        """Agent with no engine_type in config.yaml should still appear in list."""
        _write_agent(tmp_path, "old-agent", {"description": "legacy"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.get("/api/agents")
            assert resp.status_code == 200
            agents = resp.json()["agents"]
            found = [a for a in agents if a["name"] == "old-agent"]
            assert len(found) == 1
            assert found[0]["engine_type"] is None

    def test_old_agent_without_engine_type_loads_in_get(self, tmp_path):
        """Agent with no engine_type in config.yaml should still be retrievable."""
        _write_agent(tmp_path, "old-get-agent", {"description": "legacy"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            resp = client.get("/api/agents/old-get-agent")
            assert resp.status_code == 200
            assert resp.json()["engine_type"] is None

    def test_config_yaml_persists_canonical_for_alias_on_update(self, tmp_path):
        """Verify config.yaml on disk has canonical after update with alias."""
        _write_agent(tmp_path, "upd-persist", {"description": "d"})

        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            client.put("/api/agents/upd-persist", json={"engine_type": "ReadOnly_Explorer"})

        config_file = tmp_path / "agents" / "upd-persist" / "config.yaml"
        with open(config_file) as f:
            data = yaml.safe_load(f)
        assert data["engine_type"] == "read_only_explorer"

    def test_create_all_canonical_engine_types(self, tmp_path):
        """Create agents with each canonical engine_type and verify round-trip."""
        from src.gateway.app import app

        with self._patch_paths(tmp_path):
            client = TestClient(app)
            for engine in ["default", "react", "read_only_explorer", "sop"]:
                safe_name = engine.replace("_", "-")
                resp = client.post("/api/agents", json={
                    "name": f"agent-{safe_name}",
                    "description": f"test {engine}",
                    "engine_type": engine,
                    "soul": "You are helpful.",
                })
                assert resp.status_code == 201, f"Failed for engine_type={engine}"
                assert resp.json()["engine_type"] == engine


# ===========================================================================
# 9. Config Loader Tests
# ===========================================================================


class TestConfigLoaderEngineType:
    """AgentConfig loader handles engine_type from YAML correctly."""

    def _patch_paths(self, tmp_path):
        paths = _make_paths(tmp_path)
        return _multi_patch(paths)

    def test_loads_engine_type_from_yaml(self, tmp_path):
        _write_agent(tmp_path, "cfg-agent", {"description": "d", "engine_type": "react"})
        from src.config.agents_config import load_agent_config

        with self._patch_paths(tmp_path):
            cfg = load_agent_config("cfg-agent")
            assert cfg.engine_type == "react"

    def test_loads_missing_engine_type_as_none(self, tmp_path):
        _write_agent(tmp_path, "no-eng-agent", {"description": "d"})
        from src.config.agents_config import load_agent_config

        with self._patch_paths(tmp_path):
            cfg = load_agent_config("no-eng-agent")
            assert cfg.engine_type is None

    def test_loads_alias_engine_type_raw(self, tmp_path):
        """Config loader stores raw value, normalization happens at registry/API level."""
        _write_agent(tmp_path, "alias-cfg", {"description": "d", "engine_type": "ReAct"})
        from src.config.agents_config import load_agent_config

        with self._patch_paths(tmp_path):
            cfg = load_agent_config("alias-cfg")
            assert cfg.engine_type == "ReAct"

    def test_loads_unknown_engine_type_raw(self, tmp_path):
        """Config loader doesn't reject unknown engine_type values."""
        _write_agent(tmp_path, "unknown-cfg", {"description": "d", "engine_type": "bogus_engine"})
        from src.config.agents_config import load_agent_config

        with self._patch_paths(tmp_path):
            cfg = load_agent_config("unknown-cfg")
            assert cfg.engine_type == "bogus_engine"


# ===========================================================================
# 10. Tool Filter Tests
# ===========================================================================


class TestToolFilter:
    """Tests for the MCP tool filter used by read_only_explorer."""

    def test_is_read_only_tool_read_tool(self):
        from src.mcp.tool_filter import is_read_only_tool
        tool = SimpleNamespace(name="get_contacts")
        assert is_read_only_tool(tool) is True

    def test_is_read_only_tool_write_tool(self):
        from src.mcp.tool_filter import is_read_only_tool
        tool = SimpleNamespace(name="create_contact")
        assert is_read_only_tool(tool) is False

    def test_is_read_only_tool_delete(self):
        from src.mcp.tool_filter import is_read_only_tool
        tool = SimpleNamespace(name="delete_record")
        assert is_read_only_tool(tool) is False

    def test_is_read_only_tool_update(self):
        from src.mcp.tool_filter import is_read_only_tool
        tool = SimpleNamespace(name="update_setting")
        assert is_read_only_tool(tool) is False

    def test_write_keywords_coverage(self):
        from src.mcp.tool_filter import WRITE_KEYWORDS
        expected = {"write", "create", "update", "delete", "cancel", "insert", "modify", "submit"}
        assert WRITE_KEYWORDS == expected

    def test_filter_read_only_tools_preserves_count(self):
        from src.mcp.tool_filter import filter_read_only_tools
        tools = [
            SimpleNamespace(name="get_a"),
            SimpleNamespace(name="list_b"),
            SimpleNamespace(name="create_c"),
        ]
        result = filter_read_only_tools(tools)
        assert len(result) == 2
        assert all(t.name in ("get_a", "list_b") for t in result)
