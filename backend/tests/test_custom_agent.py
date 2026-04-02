"""Tests for custom agent support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from src.config.paths import Paths

    return Paths(base_dir=base_dir)


def _write_agent(
    base_dir: Path,
    name: str,
    config: dict,
    soul: str = "You are helpful.",
    prompt_file: str = "SOUL.md",
) -> None:
    """Write an agent directory with config.yaml and prompt file."""
    agent_dir = base_dir / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name

    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)

    (agent_dir / prompt_file).write_text(soul, encoding="utf-8")


# ===========================================================================
# 1. Paths class - agent path methods
# ===========================================================================


class TestPaths:
    def test_agents_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agents_dir == tmp_path / "agents"

    def test_agent_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_dir("code-reviewer") == tmp_path / "agents" / "code-reviewer"

    def test_agent_memory_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_memory_file("code-reviewer") == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_user_md_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.user_md_file == tmp_path / "USER.md"

    def test_paths_are_different_from_global(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.memory_file != paths.agent_memory_file("my-agent")
        assert paths.memory_file == tmp_path / "memory.json"
        assert paths.agent_memory_file("my-agent") == tmp_path / "agents" / "my-agent" / "memory.json"

    def test_base_dir_uses_backend_deer_flow_when_running_from_repo_root(self, tmp_path, monkeypatch):
        backend_data_dir = tmp_path / "backend" / ".deer-flow"
        backend_data_dir.mkdir(parents=True)

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DEER_FLOW_HOME", raising=False)

        from src.config.paths import Paths

        assert Paths().base_dir == backend_data_dir.resolve()


# ===========================================================================
# 2. AgentConfig - Pydantic parsing
# ===========================================================================


class TestAgentConfig:
    def test_minimal_config(self):
        from src.config.agents_config import AgentConfig

        cfg = AgentConfig(name="my-agent")
        assert cfg.name == "my-agent"
        assert cfg.description == ""
        assert cfg.model is None
        assert cfg.tool_groups is None

    def test_full_config(self):
        from src.config.agents_config import AgentConfig, McpBindingConfig

        cfg = AgentConfig(
            name="code-reviewer",
            description="Specialized for code review",
            model="deepseek-v3",
            tool_groups=["file:read", "bash"],
            domain="code",
            system_prompt_file="reviewer.md",
            hitl_keywords=["confirm", "approve"],
            max_tool_calls=12,
            mcp_binding=McpBindingConfig(domain=["code-mcp"]),
            available_skills=["code-review"],
            requested_orchestration_mode="workflow",
        )
        assert cfg.name == "code-reviewer"
        assert cfg.model == "deepseek-v3"
        assert cfg.tool_groups == ["file:read", "bash"]
        assert cfg.domain == "code"
        assert cfg.system_prompt_file == "reviewer.md"
        assert cfg.hitl_keywords == ["confirm", "approve"]
        assert cfg.max_tool_calls == 12
        assert cfg.mcp_binding.domain == ["code-mcp"]
        assert cfg.available_skills == ["code-review"]
        assert cfg.requested_orchestration_mode == "workflow"

    def test_config_from_dict(self):
        from src.config.agents_config import AgentConfig

        data = {
            "name": "test-agent",
            "description": "A test",
            "model": "gpt-4",
            "domain": "general",
            "system_prompt_file": "system.md",
            "hitl_keywords": ["escalate"],
            "max_tool_calls": 9,
            "mcp_binding": {"domain": ["directory"]},
            "available_skills": ["search"],
            "requested_orchestration_mode": "leader",
        }
        cfg = AgentConfig(**data)
        assert cfg.name == "test-agent"
        assert cfg.model == "gpt-4"
        assert cfg.tool_groups is None
        assert cfg.mcp_binding.domain == ["directory"]
        assert cfg.available_skills == ["search"]
        assert cfg.requested_orchestration_mode == "leader"


# ===========================================================================
# 3. load_agent_config
# ===========================================================================


class TestLoadAgentConfig:
    def test_load_valid_config(self, tmp_path):
        config_dict = {"name": "code-reviewer", "description": "Code review agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "code-reviewer", config_dict)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            cfg = load_agent_config("code-reviewer")

        assert cfg.name == "code-reviewer"
        assert cfg.description == "Code review agent"
        assert cfg.model == "deepseek-v3"

    def test_load_missing_agent_raises(self, tmp_path):
        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("nonexistent-agent")

    def test_load_missing_config_yaml_raises(self, tmp_path):
        # Create directory without config.yaml
        (tmp_path / "agents" / "broken-agent").mkdir(parents=True)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("broken-agent")

    def test_load_config_infers_name_from_dir(self, tmp_path):
        """Config without 'name' field should use directory name."""
        agent_dir = tmp_path / "agents" / "inferred-name"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("description: My agent\n")
        (agent_dir / "SOUL.md").write_text("Hello")

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            cfg = load_agent_config("inferred-name")

        assert cfg.name == "inferred-name"

    def test_load_config_with_tool_groups(self, tmp_path):
        config_dict = {"name": "restricted", "tool_groups": ["file:read", "file:write"]}
        _write_agent(tmp_path, "restricted", config_dict)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            cfg = load_agent_config("restricted")

        assert cfg.tool_groups == ["file:read", "file:write"]

    def test_legacy_prompt_file_field_ignored(self, tmp_path):
        """Unknown fields like the old prompt_file should be silently ignored."""
        agent_dir = tmp_path / "agents" / "legacy-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: legacy-agent\nprompt_file: system.md\n")
        (agent_dir / "SOUL.md").write_text("Soul content")

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            cfg = load_agent_config("legacy-agent")

        assert cfg.name == "legacy-agent"

    def test_load_config_resolves_env_vars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_DOMAIN_VAR", "my-domain")
        config_dict = {
            "name": "env-agent",
            "domain": "$TEST_DOMAIN_VAR",
        }
        _write_agent(tmp_path, "env-agent", config_dict)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            cfg = load_agent_config("env-agent")

        assert cfg.domain == "my-domain"

    def test_load_config_missing_env_var_raises(self, tmp_path):
        config_dict = {
            "name": "missing-env-agent",
            "domain": "$MISSING_ENV_VAR",
        }
        _write_agent(tmp_path, "missing-env-agent", config_dict)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            with pytest.raises(ValueError, match="Environment variable MISSING_ENV_VAR not found"):
                load_agent_config("missing-env-agent")


# ===========================================================================
# 4. load_agent_soul
# ===========================================================================


class TestLoadAgentSoul:
    def test_reads_soul_file(self, tmp_path):
        expected_soul = "You are a specialized code review expert."
        _write_agent(tmp_path, "code-reviewer", {"name": "code-reviewer"}, soul=expected_soul)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="code-reviewer")
            soul = load_agent_soul(cfg.name)

        assert soul == expected_soul

    def test_missing_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "no-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: no-soul\n")
        # No SOUL.md created

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="no-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_empty_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "empty-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: empty-soul\n")
        (agent_dir / "SOUL.md").write_text("   \n   ")

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="empty-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None


# ===========================================================================
# 5. list_custom_agents
# ===========================================================================


class TestListCustomAgents:
    def test_empty_when_no_agents_dir(self, tmp_path):
        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert agents == []

    def test_discovers_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "agent-a", {"name": "agent-a"})
        _write_agent(tmp_path, "agent-b", {"name": "agent-b", "description": "B"})

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_skips_dirs_without_config_yaml(self, tmp_path):
        # Valid agent
        _write_agent(tmp_path, "valid-agent", {"name": "valid-agent"})
        # Invalid dir (no config.yaml)
        (tmp_path / "agents" / "invalid-dir").mkdir(parents=True)

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "valid-agent"

    def test_skips_non_directory_entries(self, tmp_path):
        # Create the agents dir with a file (not a dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "not-a-dir.txt").write_text("hello")
        _write_agent(tmp_path, "real-agent", {"name": "real-agent"})

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "real-agent"

    def test_returns_sorted_by_name(self, tmp_path):
        _write_agent(tmp_path, "z-agent", {"name": "z-agent"})
        _write_agent(tmp_path, "a-agent", {"name": "a-agent"})
        _write_agent(tmp_path, "m-agent", {"name": "m-agent"})

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert names == sorted(names)


# ===========================================================================
# 7. Memory isolation: _get_memory_file_path
# ===========================================================================


class TestMemoryFilePath:
    def test_global_memory_path(self, tmp_path):
        """None agent_name should return global memory file."""
        import src.agents.memory.updater as updater_mod
        from src.config.memory_config import MemoryConfig

        with (
            patch("src.agents.memory.updater.get_paths", return_value=_make_paths(tmp_path)),
            patch("src.agents.memory.updater.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            path = updater_mod._get_memory_file_path(None)
        assert path == tmp_path / "memory.json"

    def test_agent_memory_path(self, tmp_path):
        """Providing agent_name should return per-agent memory file."""
        import src.agents.memory.updater as updater_mod
        from src.config.memory_config import MemoryConfig

        with (
            patch("src.agents.memory.updater.get_paths", return_value=_make_paths(tmp_path)),
            patch("src.agents.memory.updater.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            path = updater_mod._get_memory_file_path("code-reviewer")
        assert path == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_different_paths_for_different_agents(self, tmp_path):
        import src.agents.memory.updater as updater_mod
        from src.config.memory_config import MemoryConfig

        with (
            patch("src.agents.memory.updater.get_paths", return_value=_make_paths(tmp_path)),
            patch("src.agents.memory.updater.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            path_global = updater_mod._get_memory_file_path(None)
            path_a = updater_mod._get_memory_file_path("agent-a")
            path_b = updater_mod._get_memory_file_path("agent-b")

        assert path_global != path_a
        assert path_global != path_b
        assert path_a != path_b


# ===========================================================================
# 8. Gateway API - Agents endpoints
# ===========================================================================


def _make_test_app(tmp_path: Path):
    """Create a FastAPI app with the agents router, patching paths to tmp_path."""
    from fastapi import FastAPI

    from src.gateway.routers.agents import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def agent_client(tmp_path):
    """TestClient with agents router, using tmp_path as base_dir."""
    paths_instance = _make_paths(tmp_path)

    with patch("src.config.agents_config.get_paths", return_value=paths_instance), patch("src.gateway.routers.agents.get_paths", return_value=paths_instance):
        app = _make_test_app(tmp_path)
        with TestClient(app) as client:
            client._tmp_path = tmp_path  # type: ignore[attr-defined]
            yield client


class TestAgentsAPI:
    def test_list_agents_empty(self, agent_client):
        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert data["agents"] == []

    def test_create_agent(self, agent_client):
        payload = {
            "name": "code-reviewer",
            "description": "Reviews code",
            "soul": "You are a code reviewer.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "code-reviewer"
        assert data["description"] == "Reviews code"
        assert data["soul"] == "You are a code reviewer."

    def test_create_agent_invalid_name(self, agent_client):
        payload = {"name": "Code Reviewer!", "soul": "test"}
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 422

    def test_create_duplicate_agent_409(self, agent_client):
        payload = {"name": "my-agent", "soul": "test"}
        agent_client.post("/api/agents", json=payload)

        # Second create should fail
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 409

    def test_create_agent_rejects_invalid_orchestration_mode(self, agent_client):
        response = agent_client.post(
            "/api/agents",
            json={
                "name": "bad-mode-agent",
                "requested_orchestration_mode": "invalid",
                "soul": "test",
            },
        )
        assert response.status_code == 422

    def test_list_agents_after_create(self, agent_client):
        agent_client.post("/api/agents", json={"name": "agent-one", "soul": "p1"})
        agent_client.post("/api/agents", json={"name": "agent-two", "soul": "p2"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        names = [a["name"] for a in response.json()["agents"]]
        assert "agent-one" in names
        assert "agent-two" in names

    def test_get_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "test-agent", "soul": "Hello world"})

        response = agent_client.get("/api/agents/test-agent")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-agent"
        assert data["soul"] == "Hello world"

    def test_get_missing_agent_404(self, agent_client):
        response = agent_client.get("/api/agents/nonexistent")
        assert response.status_code == 404

    def test_update_agent_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "update-me", "soul": "original"})

        response = agent_client.put("/api/agents/update-me", json={"soul": "updated"})
        assert response.status_code == 200
        assert response.json()["soul"] == "updated"

    def test_update_agent_description(self, agent_client):
        agent_client.post("/api/agents", json={"name": "desc-agent", "description": "old desc", "soul": "p"})

        response = agent_client.put("/api/agents/desc-agent", json={"description": "new desc"})
        assert response.status_code == 200
        assert response.json()["description"] == "new desc"

    def test_update_missing_agent_404(self, agent_client):
        response = agent_client.put("/api/agents/ghost-agent", json={"soul": "new"})
        assert response.status_code == 404

    def test_delete_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "del-me", "soul": "bye"})

        response = agent_client.delete("/api/agents/del-me")
        assert response.status_code == 204

        # Verify it's gone
        response = agent_client.get("/api/agents/del-me")
        assert response.status_code == 404

    def test_delete_missing_agent_404(self, agent_client):
        response = agent_client.delete("/api/agents/does-not-exist")
        assert response.status_code == 404

    def test_create_agent_with_model_and_tool_groups(self, agent_client):
        payload = {
            "name": "specialized",
            "description": "Specialized agent",
            "model": "deepseek-v3",
            "tool_groups": ["file:read", "bash"],
            "soul": "You are specialized.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "deepseek-v3"
        assert data["tool_groups"] == ["file:read", "bash"]

    def test_create_persists_files_on_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "disk-check", "soul": "disk soul"})

        agent_dir = tmp_path / "agents" / "disk-check"
        assert agent_dir.exists()
        assert (agent_dir / "config.yaml").exists()
        assert (agent_dir / "SOUL.md").exists()
        assert (agent_dir / "SOUL.md").read_text() == "disk soul"

    def test_delete_removes_files_from_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "remove-me", "soul": "bye"})
        agent_dir = tmp_path / "agents" / "remove-me"
        assert agent_dir.exists()

        agent_client.delete("/api/agents/remove-me")
        assert not agent_dir.exists()


# ===========================================================================
# 9. Gateway API - User Profile endpoints
# ===========================================================================


class TestUserProfileAPI:
    def test_get_user_profile_empty(self, agent_client):
        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] is None

    def test_put_user_profile(self, agent_client, tmp_path):
        content = "# User Profile\n\nI am a developer."
        response = agent_client.put("/api/user-profile", json={"content": content})
        assert response.status_code == 200
        assert response.json()["content"] == content

        # File should be written to disk
        user_md = tmp_path / "USER.md"
        assert user_md.exists()
        assert user_md.read_text(encoding="utf-8") == content

    def test_get_user_profile_after_put(self, agent_client):
        content = "# Profile\n\nI work on data science."
        agent_client.put("/api/user-profile", json={"content": content})

        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] == content

    def test_put_empty_user_profile_returns_none(self, agent_client):
        response = agent_client.put("/api/user-profile", json={"content": ""})
        assert response.status_code == 200
        assert response.json()["content"] is None


# ===========================================================================
# 10. Phase 1 acceptance coverage
# ===========================================================================


class TestPhaseOneAgentConfig:
    def test_load_config_with_orchestration_fields(self, tmp_path):
        config_dict = {
            "name": "domain-agent",
            "description": "Handles employee lookups",
            "domain": "employee_directory",
            "system_prompt_file": "DOMAIN.md",
            "hitl_keywords": ["confirm", "approval"],
            "max_tool_calls": 7,
            "mcp_binding": {"domain": ["hr-mcp"]},
            "available_skills": ["hr"],
            "requested_orchestration_mode": "workflow",
        }
        agent_dir = tmp_path / "agents" / "domain-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text(yaml.dump(config_dict), encoding="utf-8")
        (agent_dir / "DOMAIN.md").write_text("Domain prompt", encoding="utf-8")

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_config

            cfg = load_agent_config("domain-agent")

        assert cfg.domain == "employee_directory"
        assert cfg.system_prompt_file == "DOMAIN.md"
        assert cfg.hitl_keywords == ["confirm", "approval"]
        assert cfg.max_tool_calls == 7
        assert cfg.mcp_binding.domain == ["hr-mcp"]
        assert cfg.available_skills == ["hr"]
        assert cfg.requested_orchestration_mode == "workflow"

    def test_load_agent_soul_uses_system_prompt_file(self, tmp_path):
        agent_dir = tmp_path / "agents" / "custom-prompt-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "custom-prompt-agent",
                    "system_prompt_file": "SYSTEM.md",
                }
            ),
            encoding="utf-8",
        )
        (agent_dir / "SYSTEM.md").write_text("Use the custom prompt file", encoding="utf-8")

        with patch("src.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from src.config.agents_config import load_agent_soul

            soul = load_agent_soul("custom-prompt-agent")

        assert soul == "Use the custom prompt file"


class TestPhaseOneAgentsAPI:
    def test_create_agent_persists_orchestration_fields(self, agent_client, tmp_path):
        payload = {
            "name": "employee-agent",
            "description": "Employee directory specialist",
            "domain": "employee_directory",
            "system_prompt_file": "DOMAIN.md",
            "hitl_keywords": ["confirm", "manager approval"],
            "max_tool_calls": 5,
            "tool_groups": ["search"],
            "mcp_binding": {"domain": ["employee-directory"]},
            "available_skills": ["directory", "people"],
            "requested_orchestration_mode": "workflow",
            "soul": "You are the employee directory specialist.",
        }

        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["domain"] == "employee_directory"
        assert data["system_prompt_file"] == "DOMAIN.md"
        assert data["hitl_keywords"] == ["confirm", "manager approval"]
        assert data["max_tool_calls"] == 5
        assert data["mcp_binding"]["domain"] == ["employee-directory"]
        assert data["available_skills"] == ["directory", "people"]
        assert data["requested_orchestration_mode"] == "workflow"

        config_data = yaml.safe_load((tmp_path / "agents" / "employee-agent" / "config.yaml").read_text(encoding="utf-8"))
        assert config_data["domain"] == "employee_directory"
        assert config_data["system_prompt_file"] == "DOMAIN.md"
        assert config_data["hitl_keywords"] == ["confirm", "manager approval"]
        assert config_data["max_tool_calls"] == 5
        assert config_data["mcp_binding"]["domain"] == ["employee-directory"]
        assert config_data["available_skills"] == ["directory", "people"]
        assert config_data["requested_orchestration_mode"] == "workflow"
        assert (tmp_path / "agents" / "employee-agent" / "DOMAIN.md").read_text(encoding="utf-8") == payload["soul"]

    def test_get_and_list_agents_include_orchestration_fields(self, agent_client):
        agent_client.post(
            "/api/agents",
            json={
                "name": "planner-agent",
                "domain": "planning",
                "system_prompt_file": "PLANNER.md",
                "hitl_keywords": ["clarify"],
                "max_tool_calls": 8,
                "available_skills": ["planning"],
                "requested_orchestration_mode": "leader",
                "soul": "You plan tasks.",
            },
        )

        get_response = agent_client.get("/api/agents/planner-agent")
        assert get_response.status_code == 200
        assert get_response.json()["domain"] == "planning"
        assert get_response.json()["system_prompt_file"] == "PLANNER.md"
        assert get_response.json()["available_skills"] == ["planning"]
        assert get_response.json()["requested_orchestration_mode"] == "leader"

        list_response = agent_client.get("/api/agents")
        assert list_response.status_code == 200
        listed = list_response.json()["agents"][0]
        assert listed["domain"] == "planning"
        assert listed["max_tool_calls"] == 8
        assert listed["available_skills"] == ["planning"]
        assert listed["requested_orchestration_mode"] == "leader"

    def test_update_agent_migrates_prompt_file_without_losing_content(self, agent_client, tmp_path):
        agent_client.post(
            "/api/agents",
            json={
                "name": "migrate-agent",
                "system_prompt_file": "OLD.md",
                "soul": "Original prompt",
            },
        )

        response = agent_client.put(
            "/api/agents/migrate-agent",
            json={
                "system_prompt_file": "NEW.md",
                "domain": "migrated_domain",
                "hitl_keywords": ["approve"],
                "max_tool_calls": 11,
                "mcp_binding": {"domain": ["migrated-mcp"]},
                "available_skills": ["migrated-skill"],
                "requested_orchestration_mode": "workflow",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["system_prompt_file"] == "NEW.md"
        assert data["domain"] == "migrated_domain"
        assert data["hitl_keywords"] == ["approve"]
        assert data["max_tool_calls"] == 11
        assert data["mcp_binding"]["domain"] == ["migrated-mcp"]
        assert data["available_skills"] == ["migrated-skill"]
        assert data["requested_orchestration_mode"] == "workflow"
        assert data["soul"] == "Original prompt"
        assert (tmp_path / "agents" / "migrate-agent" / "NEW.md").read_text(encoding="utf-8") == "Original prompt"

    def test_update_agent_writes_soul_to_active_prompt_file(self, agent_client, tmp_path):
        agent_client.post(
            "/api/agents",
            json={
                "name": "prompt-agent",
                "system_prompt_file": "ACTIVE.md",
                "soul": "Before update",
            },
        )

        response = agent_client.put(
            "/api/agents/prompt-agent",
            json={
                "soul": "After update",
                "max_tool_calls": 6,
            },
        )
        assert response.status_code == 200
        assert response.json()["soul"] == "After update"
        assert response.json()["max_tool_calls"] == 6
        assert (tmp_path / "agents" / "prompt-agent" / "ACTIVE.md").read_text(encoding="utf-8") == "After update"

    def test_update_agent_can_clear_orchestration_mode(self, agent_client, tmp_path):
        agent_client.post(
            "/api/agents",
            json={
                "name": "clear-mode-agent",
                "requested_orchestration_mode": "workflow",
                "soul": "Prompt",
            },
        )

        response = agent_client.put(
            "/api/agents/clear-mode-agent",
            json={"requested_orchestration_mode": None},
        )

        assert response.status_code == 200
        assert response.json()["requested_orchestration_mode"] is None
        config_data = yaml.safe_load(
            (tmp_path / "agents" / "clear-mode-agent" / "config.yaml").read_text(
                encoding="utf-8",
            )
        )
        assert "requested_orchestration_mode" not in config_data


# ===========================================================================
# 9. list_domain_agents with allowed_agents filtering
# ===========================================================================


class TestListDomainAgentsAllowedFilter:
    """Tests for the allowed_agents parameter on list_domain_agents."""

    def test_no_filter_returns_all_domain_agents(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        base = tmp_path / "agents"
        _write_agent(tmp_path, "agent-a", {"domain": "hr"})
        _write_agent(tmp_path, "agent-b", {"domain": "finance"})
        _write_agent(tmp_path, "agent-c", {"description": "no domain"})

        agents = list_domain_agents(agents_dir=base)
        names = {a.name for a in agents}
        assert "agent-a" in names
        assert "agent-b" in names
        assert "agent-c" not in names  # no domain

    def test_allowed_agents_filters_by_name(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        base = tmp_path / "agents"
        _write_agent(tmp_path, "agent-a", {"domain": "hr"})
        _write_agent(tmp_path, "agent-b", {"domain": "finance"})
        _write_agent(tmp_path, "agent-c", {"domain": "legal"})

        agents = list_domain_agents(agents_dir=base, allowed_agents=["agent-a", "agent-c"])
        names = [a.name for a in agents]
        assert names == ["agent-a", "agent-c"]

    def test_allowed_agents_case_insensitive(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        base = tmp_path / "agents"
        _write_agent(tmp_path, "data-analyst", {"domain": "analytics"})

        agents = list_domain_agents(agents_dir=base, allowed_agents=["Data-Analyst"])
        assert len(agents) == 1
        assert agents[0].name == "data-analyst"

    def test_allowed_agents_empty_list_returns_empty(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        _write_agent(tmp_path, "agent-a", {"domain": "hr"})
        base = tmp_path / "agents"

        agents = list_domain_agents(agents_dir=base, allowed_agents=[])
        assert agents == []

    def test_allowed_agents_unknown_names_ignored(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        _write_agent(tmp_path, "agent-a", {"domain": "hr"})
        base = tmp_path / "agents"

        agents = list_domain_agents(agents_dir=base, allowed_agents=["agent-a", "nonexistent"])
        assert len(agents) == 1
        assert agents[0].name == "agent-a"

    def test_allowed_agents_none_means_no_filter(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        _write_agent(tmp_path, "agent-a", {"domain": "hr"})
        _write_agent(tmp_path, "agent-b", {"domain": "finance"})
        base = tmp_path / "agents"

        agents = list_domain_agents(agents_dir=base, allowed_agents=None)
        assert len(agents) == 2


# ===========================================================================
# 10. Batch Agent Sync API
# ===========================================================================


class TestAgentSyncAPI:
    """Tests for POST /api/agents/sync."""

    def test_sync_upsert_creates_new_agents(self, agent_client, tmp_path):
        payload = {
            "agents": [
                {"name": "sync-a", "description": "Agent A", "domain": "hr", "soul": "You are A."},
                {"name": "sync-b", "description": "Agent B", "domain": "finance", "soul": "You are B."},
            ],
            "mode": "upsert",
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert sorted(data["created"]) == ["sync-a", "sync-b"]
        assert data["updated"] == []
        assert data["deleted"] == []
        assert data["errors"] == []

        # Verify on disk
        assert (tmp_path / "agents" / "sync-a" / "config.yaml").exists()
        assert (tmp_path / "agents" / "sync-b" / "config.yaml").exists()

    def test_sync_upsert_updates_existing_agents(self, agent_client, tmp_path):
        # Pre-create an agent
        agent_client.post("/api/agents", json={"name": "sync-a", "description": "Old", "soul": "Old soul."})

        payload = {
            "agents": [
                {"name": "sync-a", "description": "New description", "soul": "New soul."},
                {"name": "sync-b", "description": "Brand new", "soul": "B soul."},
            ],
            "mode": "upsert",
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == ["sync-b"]
        assert data["updated"] == ["sync-a"]
        assert data["deleted"] == []

        # Verify update applied
        config = yaml.safe_load((tmp_path / "agents" / "sync-a" / "config.yaml").read_text(encoding="utf-8"))
        assert config["description"] == "New description"

    def test_sync_replace_deletes_unlisted_agents(self, agent_client, tmp_path):
        # Pre-create agents
        agent_client.post("/api/agents", json={"name": "keep-me", "description": "Keep", "soul": "Keep."})
        agent_client.post("/api/agents", json={"name": "delete-me", "description": "Delete", "soul": "Delete."})

        payload = {
            "agents": [
                {"name": "keep-me", "description": "Updated", "soul": "Updated soul."},
            ],
            "mode": "replace",
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == ["keep-me"]
        assert data["deleted"] == ["delete-me"]

        assert (tmp_path / "agents" / "keep-me").exists()
        assert not (tmp_path / "agents" / "delete-me").exists()

    def test_sync_rejects_duplicate_names(self, agent_client):
        payload = {
            "agents": [
                {"name": "dup-agent", "description": "First", "soul": "A."},
                {"name": "dup-agent", "description": "Second", "soul": "B."},
            ],
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 422
        assert "Duplicate" in response.json()["detail"]

    def test_sync_rejects_invalid_name(self, agent_client):
        payload = {
            "agents": [
                {"name": "invalid name!", "soul": "X."},
            ],
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 422

    def test_sync_upsert_empty_list_is_noop(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "existing", "soul": "X."})

        response = agent_client.post("/api/agents/sync", json={"agents": [], "mode": "upsert"})
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == []
        assert data["updated"] == []
        assert data["deleted"] == []
        # Existing agent is untouched in upsert mode
        assert (tmp_path / "agents" / "existing").exists()

    def test_sync_replace_empty_list_deletes_all(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "agent-x", "soul": "X."})
        agent_client.post("/api/agents", json={"name": "agent-y", "soul": "Y."})

        response = agent_client.post("/api/agents/sync", json={"agents": [], "mode": "replace"})
        assert response.status_code == 200
        data = response.json()
        assert sorted(data["deleted"]) == ["agent-x", "agent-y"]
        assert not (tmp_path / "agents" / "agent-x").exists()
        assert not (tmp_path / "agents" / "agent-y").exists()

    def test_sync_preserves_orchestration_fields(self, agent_client, tmp_path):
        payload = {
            "agents": [
                {
                    "name": "rich-agent",
                    "domain": "hr",
                    "engine_type": "react",
                    "requested_orchestration_mode": "workflow",
                    "mcp_binding": {"domain": ["hr-server"]},
                    "available_skills": ["policy-lookup"],
                    "soul": "You are an HR expert.",
                },
            ],
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 200

        config = yaml.safe_load((tmp_path / "agents" / "rich-agent" / "config.yaml").read_text(encoding="utf-8"))
        assert config["domain"] == "hr"
        assert config["requested_orchestration_mode"] == "workflow"
        assert config["mcp_binding"]["domain"] == ["hr-server"]
        assert config["available_skills"] == ["policy-lookup"]

    def test_sync_case_insensitive_name_normalization(self, agent_client, tmp_path):
        payload = {
            "agents": [
                {"name": "MyAgent", "description": "Mixed case", "soul": "Prompt."},
            ],
        }
        response = agent_client.post("/api/agents/sync", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == ["myagent"]
        assert (tmp_path / "agents" / "myagent" / "config.yaml").exists()
