from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.agents_config import AgentConfig, McpBindingConfig, McpServerEntry
from src.config.extensions_config import ExtensionsConfig, McpServerConfig, SkillStateConfig
from src.mcp.binding_resolver import resolve_binding, resolve_for_main_agent
from src.mcp.runtime_manager import McpRuntimeManager
from src.mcp.tool_filter import filter_read_only_tools, is_read_only_tool


def _server(**overrides) -> McpServerConfig:
    base = {
        "enabled": True,
        "type": "stdio",
        "command": "node",
        "args": ["server.js"],
        "category": "global",
    }
    base.update(overrides)
    return McpServerConfig(**base)


def _extensions() -> ExtensionsConfig:
    return ExtensionsConfig(
        mcp_servers={
            "global-search": _server(category="global"),
            "meeting-domain": _server(category="domain", domain="meeting"),
            "contacts-domain": _server(category="domain", domain="contacts"),
            "shared-time": _server(category="shared"),
            "disabled-shared": _server(enabled=False, category="shared"),
        },
        skills={},
    )


def test_agent_config_migrates_legacy_mcp_servers_into_domain_binding():
    cfg = AgentConfig(
        name="meeting-agent",
        mcp_servers=[
            McpServerEntry(name="meeting-domain", command="node", args=["meeting.js"]),
            McpServerEntry(name="shared-time", command="node", args=["time.js"]),
        ],
    )

    binding = cfg.get_effective_mcp_binding()

    assert binding.use_global is False
    assert binding.domain == ["meeting-domain", "shared-time"]
    assert binding.shared == []
    assert binding.ephemeral == []


def test_binding_resolver_includes_only_requested_servers_and_keeps_agent_isolation():
    extensions = _extensions()

    meeting_binding = McpBindingConfig(domain=["meeting-domain"], shared=["shared-time"])
    contacts_binding = McpBindingConfig(domain=["contacts-domain"])

    meeting_resolved = resolve_binding(meeting_binding, extensions)
    contacts_resolved = resolve_binding(contacts_binding, extensions)

    assert set(meeting_resolved) == {"meeting-domain", "shared-time"}
    assert set(contacts_resolved) == {"contacts-domain"}
    assert "contacts-domain" not in meeting_resolved
    assert "meeting-domain" not in contacts_resolved


def test_binding_resolver_supports_global_servers_for_agents_that_opt_in():
    extensions = _extensions()

    resolved = resolve_binding(
        McpBindingConfig(use_global=True, domain=["meeting-domain"]),
        extensions,
    )

    assert set(resolved) == {"global-search", "meeting-domain"}


def test_binding_resolver_falls_back_to_legacy_server_entry_when_platform_registry_missing():
    extensions = ExtensionsConfig(mcp_servers={}, skills={})
    agent = AgentConfig(
        name="contacts-agent",
        mcp_servers=[
            McpServerEntry(
                name="legacy-contacts",
                command="python",
                args=["contacts.py"],
                env={"TOKEN": "secret"},
            )
        ],
    )

    resolved = resolve_binding(
        McpBindingConfig(domain=["legacy-contacts"]),
        extensions,
        agent_config=agent,
    )

    assert set(resolved) == {"legacy-contacts"}
    assert resolved["legacy-contacts"].type == "stdio"
    assert resolved["legacy-contacts"].command == "python"
    assert resolved["legacy-contacts"].args == ["contacts.py"]
    assert resolved["legacy-contacts"].env == {"TOKEN": "secret"}
    assert resolved["legacy-contacts"].category == "domain"


def test_binding_resolver_ignores_disabled_shared_server_and_ephemeral_does_not_crash():
    extensions = _extensions()

    resolved = resolve_binding(
        McpBindingConfig(shared=["disabled-shared"], ephemeral=["reserved-server"]),
        extensions,
    )

    assert resolved == {}


def test_main_agent_only_gets_global_servers():
    resolved = resolve_for_main_agent(_extensions())

    assert set(resolved) == {"global-search"}


def test_extensions_config_filters_servers_by_category_and_name():
    extensions = _extensions()

    assert set(extensions.get_servers_by_category("shared")) == {"shared-time"}
    assert set(extensions.get_servers_by_names(["shared-time", "disabled-shared"])) == {"shared-time"}


def test_read_only_filter_blocks_write_like_tools_and_keeps_safe_tools():
    tools = [
        SimpleNamespace(name="contacts_search"),
        SimpleNamespace(name="meeting_create_event"),
        SimpleNamespace(name="approval_submit_form"),
        SimpleNamespace(name="directory_read_profile"),
        SimpleNamespace(name="schedule_modify_booking"),
    ]

    filtered = filter_read_only_tools(tools)

    assert [tool.name for tool in filtered] == ["contacts_search", "directory_read_profile"]
    assert is_read_only_tool(SimpleNamespace(name="contacts_search")) is True
    assert is_read_only_tool(SimpleNamespace(name="contacts_delete_record")) is False


def test_check_server_health_uses_default_or_custom_path_and_headers(monkeypatch):
    from src.mcp import health as health_module

    requests: list[tuple[str, dict[str, str]]] = []

    class _FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code

    class _FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers):
            requests.append((url, headers))
            return _FakeResponse(200)

    monkeypatch.setattr(health_module.httpx, "AsyncClient", _FakeClient)

    default_ok = asyncio.run(
        health_module.check_server_health(
            "meeting",
            McpServerConfig(type="sse", url="https://mcp.example.com/base/", headers={"X-Test": "1"}),
        )
    )
    custom_ok = asyncio.run(
        health_module.check_server_health(
            "contacts",
            McpServerConfig(type="http", url="https://mcp.example.com", healthcheck_path="ready"),
        )
    )

    assert default_ok is True
    assert custom_ok is True
    # Health URL is built from origin (scheme+host+port), NOT the full endpoint path.
    # e.g. url="https://mcp.example.com/base/" → origin="https://mcp.example.com" → health="https://mcp.example.com/health"
    assert requests == [
        ("https://mcp.example.com/health", {"X-Test": "1"}),
        ("https://mcp.example.com/ready", {}),
    ]


def test_check_server_health_returns_false_when_url_missing_for_remote_transport():
    from src.mcp.health import check_server_health

    result = asyncio.run(check_server_health("broken", McpServerConfig(type="sse", url=None)))

    assert result is False


def test_runtime_manager_is_lazy_reuses_scope_and_tracks_scope_errors(monkeypatch):
    manager = McpRuntimeManager()
    connect_calls: list[str] = []

    async def fake_connect(self):
        connect_calls.append(self.scope_key)
        self._tools = [SimpleNamespace(name=f"{self.scope_key}-tool")]
        self._last_error = None
        return True

    monkeypatch.setattr("src.mcp.runtime_manager._ScopedMCPClient.connect", fake_connect)

    scope = manager.scope_key_for_agent("meeting-agent")
    assert manager.is_scope_loaded(scope) is False

    first_load = asyncio.run(manager.load_scope(scope, {"meeting-domain": _server(category="domain")}))
    second_load = asyncio.run(manager.load_scope(scope, {"meeting-domain": _server(category="domain")}))
    tools = asyncio.run(manager.get_tools(scope))

    assert first_load is True
    assert second_load is True
    assert manager.is_scope_loaded(scope) is True
    assert connect_calls == [scope, scope]
    assert [tool.name for tool in tools] == [f"{scope}-tool"]
    assert manager.get_scope_error(scope) is None


def test_runtime_manager_returns_empty_for_unloaded_scope_and_can_unload(monkeypatch):
    manager = McpRuntimeManager()
    disconnect_calls: list[str] = []

    async def fake_connect(self):
        self._tools = [SimpleNamespace(name="global-tool")]
        return True

    async def fake_disconnect(self):
        disconnect_calls.append(self.scope_key)
        self._tools = None
        self._client = None
        self._last_error = None

    monkeypatch.setattr("src.mcp.runtime_manager._ScopedMCPClient.connect", fake_connect)
    monkeypatch.setattr("src.mcp.runtime_manager._ScopedMCPClient.disconnect", fake_disconnect)

    assert asyncio.run(manager.get_tools("global")) == []

    asyncio.run(manager.load_scope("global", {"global-search": _server()}))
    assert [tool.name for tool in manager.get_tools_sync("global")] == ["global-tool"]

    asyncio.run(manager.unload_scope("global"))

    assert disconnect_calls == ["global"]
    assert manager.is_scope_loaded("global") is False


def test_runtime_manager_shutdown_disconnects_all_loaded_scopes(monkeypatch):
    manager = McpRuntimeManager()
    disconnected: list[str] = []

    async def fake_connect(self):
        self._tools = [SimpleNamespace(name=self.scope_key)]
        return True

    async def fake_disconnect(self):
        disconnected.append(self.scope_key)
        self._tools = None

    monkeypatch.setattr("src.mcp.runtime_manager._ScopedMCPClient.connect", fake_connect)
    monkeypatch.setattr("src.mcp.runtime_manager._ScopedMCPClient.disconnect", fake_disconnect)

    asyncio.run(manager.load_scope("global", {"global-search": _server()}))
    asyncio.run(manager.load_scope(manager.scope_key_for_agent("contacts-agent"), {"contacts-domain": _server(category="domain")}))

    asyncio.run(manager.shutdown())

    assert set(disconnected) == {"global", "domain:contacts-agent"}
    assert manager.is_scope_loaded("global") is False
    assert manager.is_scope_loaded("domain:contacts-agent") is False


def _make_mcp_app() -> FastAPI:
    from src.gateway.routers.mcp import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_mcp_router_get_returns_new_fields_without_breaking_contract(monkeypatch):
    from src.gateway.routers import mcp as mcp_router

    config = ExtensionsConfig(
        mcp_servers={
            "meeting-domain": McpServerConfig(
                type="sse",
                url="https://example.com/sse",
                headers={"Authorization": "Bearer token"},
                healthcheck_path="/health",
                connect_timeout_seconds=11,
                call_timeout_seconds=22,
                retry_count=3,
                circuit_breaker_enabled=True,
                category="domain",
                domain="meeting",
                readonly=True,
            )
        },
        skills={},
    )

    monkeypatch.setattr(mcp_router, "get_extensions_config", lambda: config)

    with TestClient(_make_mcp_app()) as client:
        response = client.get("/api/mcp/config")

    assert response.status_code == 200
    data = response.json()["mcp_servers"]["meeting-domain"]
    assert data["type"] == "sse"
    assert data["url"] == "https://example.com/sse"
    assert data["headers"] == {"Authorization": "Bearer token"}
    assert data["healthcheck_path"] == "/health"
    assert data["connect_timeout_seconds"] == 11
    assert data["call_timeout_seconds"] == 22
    assert data["retry_count"] == 3
    assert data["circuit_breaker_enabled"] is True
    assert data["category"] == "domain"
    assert data["domain"] == "meeting"
    assert data["readonly"] is True


def test_mcp_router_put_persists_mcp_servers_and_preserves_skills(monkeypatch, tmp_path):
    from src.gateway.routers import mcp as mcp_router

    config_path = tmp_path / "extensions_config.json"
    current_config = ExtensionsConfig(
        mcp_servers={},
        skills={"skill-a": SkillStateConfig(enabled=True)},
    )
    reloaded_config = ExtensionsConfig(
        mcp_servers={
            "shared-time": McpServerConfig(
                type="http",
                url="https://example.com/mcp",
                category="shared",
                readonly=False,
            )
        },
        skills={"skill-a": SkillStateConfig(enabled=True)},
    )

    monkeypatch.setattr(mcp_router.ExtensionsConfig, "resolve_config_path", classmethod(lambda cls, config_path=None: config_path or config_path))
    monkeypatch.setattr(mcp_router, "get_extensions_config", lambda: current_config)
    monkeypatch.setattr(mcp_router, "reload_extensions_config", lambda: reloaded_config)
    monkeypatch.setattr(mcp_router.Path, "cwd", classmethod(lambda cls: tmp_path / "backend"))

    with TestClient(_make_mcp_app()) as client:
        response = client.put(
            "/api/mcp/config",
            json={
                "mcp_servers": {
                    "shared-time": {
                        "enabled": True,
                        "type": "http",
                        "url": "https://example.com/mcp",
                        "headers": {},
                        "healthcheck_path": "/health",
                        "connect_timeout_seconds": 5,
                        "call_timeout_seconds": 10,
                        "retry_count": 1,
                        "circuit_breaker_enabled": False,
                        "category": "shared",
                        "domain": None,
                        "readonly": False,
                        "description": "",
                        "args": [],
                        "env": {},
                        "command": None,
                        "oauth": None,
                    }
                }
            },
        )

    assert response.status_code == 200
    persisted = config_path.read_text(encoding="utf-8")
    assert '"mcpServers"' in persisted
    assert '"shared-time"' in persisted
    assert '"skills"' in persisted
    assert '"skill-a"' in persisted


def test_mcp_router_put_returns_500_when_write_fails(monkeypatch):
    from src.gateway.routers import mcp as mcp_router

    monkeypatch.setattr(mcp_router.ExtensionsConfig, "resolve_config_path", classmethod(lambda cls, config_path=None: Path("E:/broken/extensions_config.json")))
    monkeypatch.setattr(mcp_router, "get_extensions_config", lambda: ExtensionsConfig(mcp_servers={}, skills={}))
    monkeypatch.setattr("builtins.open", Mock(side_effect=OSError("disk full")))

    with TestClient(_make_mcp_app()) as client:
        response = client.put("/api/mcp/config", json={"mcp_servers": {}})

    assert response.status_code == 500
    assert "Failed to update MCP configuration" in response.json()["detail"]
