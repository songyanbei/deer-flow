"""Tests for agent sync dependency validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


def _make_paths(base_dir: Path):
    from src.config.paths import Paths
    return Paths(base_dir=base_dir)


def _make_test_app():
    from src.gateway.routers.agents import router

    app = FastAPI()

    class _MockIdentityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.tenant_id = "default"
            request.state.user_id = "test-user"
            request.state.role = "admin"
            return await call_next(request)

    app.add_middleware(_MockIdentityMiddleware)
    app.include_router(router)
    return app


@pytest.fixture()
def sync_client(tmp_path: Path):
    paths_instance = _make_paths(tmp_path)
    (tmp_path / "agents").mkdir(parents=True)

    ext_cfg_path = tmp_path / "extensions_config.json"
    ext_cfg_path.write_text(json.dumps({
        "mcpServers": {
            "github": {"enabled": True, "type": "sse"},
            "gitlab": {"enabled": True, "type": "sse"},
        },
        "skills": {},
    }))

    skills_dir = tmp_path / "skills"
    (skills_dir / "public").mkdir(parents=True)
    (skills_dir / "custom").mkdir(parents=True)

    demo_skill = skills_dir / "public" / "code-review"
    demo_skill.mkdir(parents=True)
    (demo_skill / "SKILL.md").write_text("---\nname: code-review\ndescription: Reviews code\n---\n")

    with (
        patch("src.config.agents_config.get_paths", return_value=paths_instance),
        patch("src.gateway.routers.agents.get_paths", return_value=paths_instance),
        patch("src.config.extensions_config.ExtensionsConfig.from_tenant") as mock_from_tenant,
        patch("src.skills.loader.get_skills_root_path", return_value=skills_dir),
    ):
        from src.config.extensions_config import ExtensionsConfig
        mock_from_tenant.side_effect = lambda tid: ExtensionsConfig.model_validate(json.loads(ext_cfg_path.read_text()))

        app = _make_test_app()
        with TestClient(app) as client:
            yield client


class TestAgentSyncDependencyValidation:
    def test_valid_dependencies_pass(self, sync_client):
        payload = {
            "agents": [{
                "name": "my-agent",
                "description": "test",
                "soul": "Hello",
                "mcp_binding": {"domain": ["github"], "shared": ["gitlab"]},
                "available_skills": ["code-review"],
            }],
            "validate_dependencies": True,
        }
        resp = sync_client.post("/api/agents/sync", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "my-agent" in data["created"]
        assert data["errors"] == []

    def test_missing_mcp_server_fails(self, sync_client):
        payload = {
            "agents": [{
                "name": "bad-agent",
                "description": "test",
                "soul": "Hello",
                "mcp_binding": {"domain": ["nonexistent-mcp"]},
            }],
            "validate_dependencies": True,
        }
        resp = sync_client.post("/api/agents/sync", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) == 1
        assert "nonexistent-mcp" in data["errors"][0]["error"]
        assert data["errors"][0]["action"] == "failed"

    def test_missing_skill_fails(self, sync_client):
        payload = {
            "agents": [{
                "name": "no-skill-agent",
                "description": "test",
                "soul": "Hello",
                "available_skills": ["nonexistent-skill"],
            }],
            "validate_dependencies": True,
        }
        resp = sync_client.post("/api/agents/sync", json=payload)
        data = resp.json()
        assert len(data["errors"]) == 1
        assert "nonexistent-skill" in data["errors"][0]["error"]

    def test_validate_false_skips_check(self, sync_client):
        payload = {
            "agents": [{
                "name": "skip-agent",
                "description": "test",
                "soul": "Hello",
                "mcp_binding": {"domain": ["nonexistent-mcp"]},
                "available_skills": ["nonexistent-skill"],
            }],
            "validate_dependencies": False,
        }
        resp = sync_client.post("/api/agents/sync", json=payload)
        data = resp.json()
        assert "skip-agent" in data["created"]
        assert data["errors"] == []

    def test_partial_failure_does_not_block_others(self, sync_client):
        payload = {
            "agents": [
                {
                    "name": "good-agent",
                    "description": "ok",
                    "soul": "Hi",
                    "mcp_binding": {"domain": ["github"]},
                },
                {
                    "name": "bad-agent",
                    "description": "not ok",
                    "soul": "Hi",
                    "mcp_binding": {"domain": ["nonexistent"]},
                },
            ],
            "validate_dependencies": True,
        }
        resp = sync_client.post("/api/agents/sync", json=payload)
        data = resp.json()
        assert "good-agent" in data["created"]
        assert len(data["errors"]) == 1
        assert data["errors"][0]["name"] == "bad-agent"
