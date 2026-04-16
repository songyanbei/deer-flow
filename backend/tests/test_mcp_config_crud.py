"""Tests for MCP config single-item CRUD and source gating."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


def _make_paths(base_dir: Path):
    from src.config.paths import Paths
    return Paths(base_dir=base_dir)


def _make_test_app():
    from src.gateway.routers.mcp import router

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
def mcp_client(tmp_path: Path):
    config_path = tmp_path / "extensions_config.json"
    config_path.write_text(json.dumps({"mcpServers": {}, "skills": {}}))

    with (
        patch("src.gateway.routers.mcp.ExtensionsConfig.resolve_config_path", return_value=config_path),
        patch("src.gateway.routers.mcp.ExtensionsConfig.from_tenant") as mock_from_tenant,
        patch("src.gateway.routers.mcp.reload_extensions_config"),
        patch("src.mcp.cache.reset_mcp_tools_cache", return_value=None),
    ):
        from src.config.extensions_config import ExtensionsConfig
        mock_from_tenant.side_effect = lambda tid: ExtensionsConfig.model_validate(json.loads(config_path.read_text()))

        app = _make_test_app()
        with TestClient(app) as client:
            client._config_path = config_path  # type: ignore
            yield client


class TestGetMcpConfig:
    def test_empty(self, mcp_client):
        resp = mcp_client.get("/api/mcp/config")
        assert resp.status_code == 200
        assert resp.json()["mcp_servers"] == {}


class TestPutMcpConfigFull:
    def test_full_replace(self, mcp_client):
        payload = {
            "mcp_servers": {
                "github": {"enabled": True, "type": "sse", "url": "https://example.com/sse", "description": "gh"}
            }
        }
        resp = mcp_client.put("/api/mcp/config", json=payload)
        assert resp.status_code == 200

        raw = json.loads(mcp_client._config_path.read_text())  # type: ignore
        assert "github" in raw["mcpServers"]


class TestPutMcpSingleItem:
    def test_create_new(self, mcp_client):
        body = {"enabled": True, "type": "sse", "url": "https://example.com", "source": "moss-portal"}
        resp = mcp_client.put("/api/mcp/config/newserver", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "newserver"
        assert data["source"] == "moss-portal"

        raw = json.loads(mcp_client._config_path.read_text())  # type: ignore
        assert "newserver" in raw["mcpServers"]
        assert raw["mcpServers"]["newserver"]["source"] == "moss-portal"

    def test_source_conflict_409(self, mcp_client):
        body1 = {"enabled": True, "type": "sse", "source": "portal-a"}
        mcp_client.put("/api/mcp/config/srv", json=body1)

        body2 = {"enabled": True, "type": "sse", "source": "portal-b"}
        resp = mcp_client.put("/api/mcp/config/srv", json=body2)
        assert resp.status_code == 409

    def test_same_source_update_ok(self, mcp_client):
        body = {"enabled": True, "type": "sse", "source": "portal-a", "description": "v1"}
        mcp_client.put("/api/mcp/config/srv", json=body)

        body["description"] = "v2"
        resp = mcp_client.put("/api/mcp/config/srv", json=body)
        assert resp.status_code == 200

    def test_null_source_allows_first_claim(self, mcp_client):
        """An entry with source=null can be claimed by any source (first-write-wins)."""
        body1 = {"enabled": True, "type": "sse"}
        mcp_client.put("/api/mcp/config/manual", json=body1)

        body2 = {"enabled": True, "type": "sse", "source": "portal"}
        resp = mcp_client.put("/api/mcp/config/manual", json=body2)
        assert resp.status_code == 200

    def test_managed_entry_rejects_null_source_update(self, mcp_client):
        """An entry with source set cannot be updated without providing the same source."""
        body1 = {"enabled": True, "type": "sse", "source": "portal-a"}
        mcp_client.put("/api/mcp/config/managed", json=body1)

        body2 = {"enabled": True, "type": "sse"}
        resp = mcp_client.put("/api/mcp/config/managed", json=body2)
        assert resp.status_code == 409


class TestDeleteMcpSingleItem:
    def test_delete_existing(self, mcp_client):
        mcp_client.put("/api/mcp/config/deleteme", json={"enabled": True, "type": "sse"})
        resp = mcp_client.delete("/api/mcp/config/deleteme")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "deleteme"

    def test_delete_not_found(self, mcp_client):
        resp = mcp_client.delete("/api/mcp/config/ghost")
        assert resp.status_code == 404

    def test_delete_source_mismatch_409(self, mcp_client):
        mcp_client.put("/api/mcp/config/owned", json={"enabled": True, "type": "sse", "source": "portal-a"})
        resp = mcp_client.delete("/api/mcp/config/owned", params={"source": "portal-b"})
        assert resp.status_code == 409

    def test_delete_managed_without_source_403(self, mcp_client):
        mcp_client.put("/api/mcp/config/managed", json={"enabled": True, "type": "sse", "source": "portal"})
        resp = mcp_client.delete("/api/mcp/config/managed")
        assert resp.status_code == 403

    def test_delete_with_matching_source(self, mcp_client):
        mcp_client.put("/api/mcp/config/srv", json={"enabled": True, "type": "sse", "source": "portal"})
        resp = mcp_client.delete("/api/mcp/config/srv", params={"source": "portal"})
        assert resp.status_code == 200


class TestSchemaFields:
    def test_source_and_mcp_kind_in_response(self, mcp_client):
        body = {"enabled": True, "type": "sse", "source": "portal", "mcp_kind": "remote"}
        mcp_client.put("/api/mcp/config/withkind", json=body)

        raw = json.loads(mcp_client._config_path.read_text())  # type: ignore
        srv = raw["mcpServers"]["withkind"]
        assert srv["source"] == "portal"
        assert srv["mcp_kind"] == "remote"
