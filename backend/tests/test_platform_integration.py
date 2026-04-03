"""End-to-end platform integration tests.

Simulates the full interaction lifecycle described in the architecture docs:
  平台接入改造实施方案 §4.3.4 — complete interaction sequence
  平台控制面与DeerFlow运行时交互契约草案 §8 — staged rollout (A/B/C)

Covers:
  1. Agent sync → thread creation → message stream (full platform flow)
  2. allowed_agents context propagation into planner/router
  3. Cross-router compatibility (runtime threads + uploads/artifacts/interventions/governance)
  4. Multi-tenant isolation across the full lifecycle
  5. Binding metadata lifecycle (create → update on success → no update on failure)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.paths import Paths
from src.gateway.thread_registry import ThreadRegistry


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_paths(tmp_path: Path) -> Paths:
    return Paths(base_dir=tmp_path)


def _write_agent_on_disk(
    agents_dir: Path,
    name: str,
    *,
    domain: str | None = None,
    soul: str = "You are helpful.",
) -> None:
    """Write a minimal agent directory (config.yaml + SOUL.md)."""
    d = agents_dir / name.lower()
    d.mkdir(parents=True, exist_ok=True)
    config: dict = {"name": name.lower(), "description": f"Test {name}"}
    if domain:
        config["domain"] = domain
    with open(d / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    (d / "SOUL.md").write_text(soul, encoding="utf-8")


class _IntegrationAppContext:
    """Manages a multi-router test app with patched globals for integration tests.

    Mounts both the agents router and the runtime router so a single TestClient
    can exercise the full platform lifecycle: sync → thread → stream.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        tenant_id: str = "default",
        user_id: str = "user-1",
        username: str = "tester",
    ):
        from src.gateway.routers import agents as agents_mod
        from src.gateway.routers import runtime as runtime_mod

        self._runtime_mod = runtime_mod
        self._agents_mod = agents_mod

        # Save originals for cleanup
        self._orig_runtime_get_registry = runtime_mod.get_thread_registry
        self._orig_runtime_resolve_agents_dir = runtime_mod._resolve_agents_dir

        # Shared registry
        self.registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")

        # Shared paths
        self.paths = _make_paths(tmp_path)
        self.agents_dir = self.paths.agents_dir
        self.agents_dir.mkdir(parents=True, exist_ok=True)

        self.tenant_id = tenant_id
        self.user_id = user_id

        # Build app
        self.app = FastAPI()

        @self.app.middleware("http")
        async def inject_identity(request, call_next):
            request.state.tenant_id = tenant_id
            request.state.user_id = user_id
            request.state.username = username
            request.state.role = "admin"
            return await call_next(request)

        self.app.include_router(agents_mod.router)
        self.app.include_router(runtime_mod.router)

        # Patch
        _registry = self.registry
        runtime_mod.get_thread_registry = lambda: _registry  # type: ignore
        _agents_dir = self.agents_dir
        runtime_mod._resolve_agents_dir = lambda tid: _agents_dir  # type: ignore

        # Patch paths for agents router
        self._paths_patches = [
            patch("src.config.agents_config.get_paths", return_value=self.paths),
            patch("src.gateway.routers.agents.get_paths", return_value=self.paths),
        ]
        for p in self._paths_patches:
            p.start()

    def cleanup(self):
        self._runtime_mod.get_thread_registry = self._orig_runtime_get_registry  # type: ignore
        self._runtime_mod._resolve_agents_dir = self._orig_runtime_resolve_agents_dir  # type: ignore
        for p in self._paths_patches:
            p.stop()


@pytest.fixture()
def integration_ctx(tmp_path):
    """Yield a fully-wired integration context, cleaning up after the test."""
    ctx = _IntegrationAppContext(tmp_path)
    try:
        yield ctx
    finally:
        ctx.cleanup()


@pytest.fixture()
def client(integration_ctx):
    """TestClient for the integration app."""
    return TestClient(integration_ctx.app)


# ── 1. Full Platform Lifecycle ───────────────────────────────────────────


class TestFullPlatformLifecycle:
    """Stage A: Agent sync → thread creation → message stream.

    Simulates the exact sequence from 实施方案 §4.3.4:
      1. POST /api/agents/sync   (初始化阶段)
      2. POST /api/runtime/threads   (运行阶段)
      3. POST /api/runtime/threads/{id}/messages:stream   (运行阶段)
    """

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_sync_then_create_then_stream(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        # Step 1: Batch sync agents
        sync_resp = client.post(
            "/api/agents/sync",
            json={
                "agents": [
                    {"name": "research-agent", "domain": "research", "description": "Research", "soul": "Research."},
                    {"name": "data-analyst", "domain": "analytics", "description": "Analytics", "soul": "Analyze."},
                    {"name": "report-agent", "domain": "reporting", "description": "Reports", "soul": "Report."},
                ],
                "mode": "upsert",
            },
        )
        assert sync_resp.status_code == 200
        sync_data = sync_resp.json()
        assert sorted(sync_data["created"]) == ["data-analyst", "report-agent", "research-agent"]
        assert sync_data["errors"] == []

        # Verify agents are listable
        list_resp = client.get("/api/agents")
        assert list_resp.status_code == 200
        agent_names = {a["name"] for a in list_resp.json()["agents"]}
        assert agent_names == {"research-agent", "data-analyst", "report-agent"}

        # Step 2: Create runtime thread
        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"thread_id": "thread-e2e-001"}
            create_resp = client.post(
                "/api/runtime/threads",
                json={"portal_session_id": "sess_e2e_001"},
            )
        assert create_resp.status_code == 201
        assert create_resp.json()["thread_id"] == "thread-e2e-001"
        assert create_resp.json()["portal_session_id"] == "sess_e2e_001"
        assert create_resp.json()["tenant_id"] == "default"
        assert create_resp.json()["user_id"] == "user-1"

        # Verify registry binding
        binding = integration_ctx.registry.get_binding("thread-e2e-001")
        assert binding is not None
        assert binding["portal_session_id"] == "sess_e2e_001"

        # Step 3: Stream message with allowed_agents
        mock_start.return_value = ("fake_chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {"thread_id": "thread-e2e-001"}\n\n'
            yield 'event: run_completed\ndata: {"thread_id": "thread-e2e-001"}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        stream_resp = client.post(
            "/api/runtime/threads/thread-e2e-001/messages:stream",
            json={
                "message": "请分析本月销售数据并给出结论",
                "group_key": "market-analysis-team",
                "allowed_agents": ["research-agent", "data-analyst"],
                "entry_agent": "research-agent",
                "requested_orchestration_mode": "workflow",
            },
        )
        assert stream_resp.status_code == 200
        assert stream_resp.headers["content-type"].startswith("text/event-stream")

        # Verify binding was updated after successful stream
        updated = integration_ctx.registry.get_binding("thread-e2e-001")
        assert updated["group_key"] == "market-analysis-team"
        assert updated["allowed_agents"] == ["research-agent", "data-analyst"]
        assert updated["entry_agent"] == "research-agent"
        assert updated["requested_orchestration_mode"] == "workflow"
        assert updated["updated_at"] != binding["updated_at"]

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_replace_mode_removes_unlisted_agents(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        """Verify replace mode delete + subsequent stream with narrowed allowlist."""
        # Sync 3 agents
        client.post("/api/agents/sync", json={
            "agents": [
                {"name": "agent-a", "domain": "a", "soul": "A."},
                {"name": "agent-b", "domain": "b", "soul": "B."},
                {"name": "agent-c", "domain": "c", "soul": "C."},
            ],
            "mode": "upsert",
        })

        # Replace: keep only agent-a, agent-b → agent-c should be deleted
        replace_resp = client.post("/api/agents/sync", json={
            "agents": [
                {"name": "agent-a", "domain": "a", "soul": "A updated."},
                {"name": "agent-b", "domain": "b", "soul": "B updated."},
            ],
            "mode": "replace",
        })
        assert replace_resp.status_code == 200
        data = replace_resp.json()
        assert data["updated"] == ["agent-a", "agent-b"]
        assert data["deleted"] == ["agent-c"]

        # Create thread and stream — agent-c no longer valid
        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-replace"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess_replace"})

        # Attempt to use deleted agent-c → 422
        stream_resp = client.post(
            "/api/runtime/threads/thread-replace/messages:stream",
            json={
                "message": "test",
                "group_key": "team",
                "allowed_agents": ["agent-c"],
            },
        )
        assert stream_resp.status_code == 422
        assert "agent-c" in stream_resp.json()["detail"]

        # Using remaining agents works
        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        ok_resp = client.post(
            "/api/runtime/threads/thread-replace/messages:stream",
            json={
                "message": "test",
                "group_key": "team",
                "allowed_agents": ["agent-a", "agent-b"],
            },
        )
        assert ok_resp.status_code == 200


# ── 2. allowed_agents Context Propagation ────────────────────────────────


class TestAllowedAgentsContextPropagation:
    """Verify allowed_agents flows from the runtime adapter into the LangGraph context."""

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_context_contains_all_required_fields(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        """Verify the context dict passed to start_stream contains all required fields."""
        _write_agent_on_disk(integration_ctx.agents_dir, "sub-1", domain="d1")
        _write_agent_on_disk(integration_ctx.agents_dir, "sub-2", domain="d2")

        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-ctx"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess_ctx"})

        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        client.post(
            "/api/runtime/threads/thread-ctx/messages:stream",
            json={
                "message": "analyze this",
                "group_key": "ctx-group",
                "allowed_agents": ["sub-1", "sub-2"],
                "entry_agent": "sub-1",
                "requested_orchestration_mode": "leader",
            },
        )

        # Inspect the context passed to start_stream
        call_kwargs = mock_start.call_args.kwargs
        ctx = call_kwargs["context"]

        assert ctx["thread_id"] == "thread-ctx"
        assert ctx["tenant_id"] == "default"
        assert ctx["user_id"] == "user-1"
        assert ctx["username"] == "tester"
        assert ctx["allowed_agents"] == ["sub-1", "sub-2"]
        assert ctx["group_key"] == "ctx-group"
        assert ctx["requested_orchestration_mode"] == "leader"
        assert ctx["agent_name"] == "sub-1"  # entry_agent → agent_name

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_context_omits_agent_name_when_no_entry_agent(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        _write_agent_on_disk(integration_ctx.agents_dir, "sub-1", domain="d1")

        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-no-entry"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess"})

        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        client.post(
            "/api/runtime/threads/thread-no-entry/messages:stream",
            json={
                "message": "hi",
                "group_key": "team",
                "allowed_agents": ["sub-1"],
            },
        )

        ctx = mock_start.call_args.kwargs["context"]
        assert "agent_name" not in ctx
        assert "requested_orchestration_mode" not in ctx

    def test_planner_receives_allowed_agents_from_configurable(self, tmp_path):
        """Verify list_domain_agents is called with allowed_agents in planner."""
        from src.config.agents_config import list_domain_agents

        agents_dir = tmp_path / "agents"
        _write_agent_on_disk(agents_dir, "alpha", domain="d1")
        _write_agent_on_disk(agents_dir, "beta", domain="d2")
        _write_agent_on_disk(agents_dir, "gamma", domain="d3")

        # Without filter — all 3
        all_agents = list_domain_agents(agents_dir=agents_dir)
        assert len(all_agents) == 3

        # With filter — only alpha, gamma
        filtered = list_domain_agents(agents_dir=agents_dir, allowed_agents=["alpha", "gamma"])
        names = [a.name for a in filtered]
        assert names == ["alpha", "gamma"]

        # Filter with unknown name — silently ignored
        filtered2 = list_domain_agents(agents_dir=agents_dir, allowed_agents=["alpha", "nonexistent"])
        assert len(filtered2) == 1
        assert filtered2[0].name == "alpha"


# ── 3. Multi-Tenant Isolation (Full Lifecycle) ───────────────────────────


class TestMultiTenantIsolation:
    """Verify tenant boundaries hold across the full lifecycle."""

    def test_tenant_a_cannot_access_tenant_b_thread(self, tmp_path):
        """Thread created by tenant-A is inaccessible to tenant-B."""
        from src.gateway.routers import runtime as runtime_mod

        # Tenant A context
        ctx_a = _IntegrationAppContext(tmp_path / "a", tenant_id="tenant-a", user_id="user-a")
        # Tenant B context sharing the same registry
        ctx_b = _IntegrationAppContext(tmp_path / "b", tenant_id="tenant-b", user_id="user-b")
        # Share registry so both tenants see the same thread store
        ctx_b.registry = ctx_a.registry
        runtime_mod.get_thread_registry = lambda: ctx_a.registry  # type: ignore

        try:
            client_a = TestClient(ctx_a.app)
            client_b = TestClient(ctx_b.app)

            # Tenant A creates a thread
            with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
                mc.return_value = {"thread_id": "thread-tenant-a"}
                resp = client_a.post(
                    "/api/runtime/threads",
                    json={"portal_session_id": "sess_a"},
                )
                assert resp.status_code == 201

            # Tenant B tries to read it → 403
            with patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock):
                resp_b = client_b.get("/api/runtime/threads/thread-tenant-a")
                assert resp_b.status_code == 403

            # Tenant B tries to stream to it → 403
            resp_stream = client_b.post(
                "/api/runtime/threads/thread-tenant-a/messages:stream",
                json={
                    "message": "hijack",
                    "group_key": "team",
                    "allowed_agents": ["any"],
                },
            )
            assert resp_stream.status_code == 403

        finally:
            ctx_a.cleanup()
            ctx_b.cleanup()

    def test_cross_owner_same_tenant_denied(self, tmp_path):
        """Two users in the same tenant cannot access each other's threads."""
        from src.gateway.routers import runtime as runtime_mod

        ctx_owner = _IntegrationAppContext(tmp_path / "owner", tenant_id="tenant-x", user_id="owner-1")
        ctx_other = _IntegrationAppContext(tmp_path / "other", tenant_id="tenant-x", user_id="other-2")
        ctx_other.registry = ctx_owner.registry
        runtime_mod.get_thread_registry = lambda: ctx_owner.registry  # type: ignore

        try:
            client_owner = TestClient(ctx_owner.app)
            client_other = TestClient(ctx_other.app)

            with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
                mc.return_value = {"thread_id": "thread-owned"}
                client_owner.post("/api/runtime/threads", json={"portal_session_id": "sess"})

            with patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock):
                resp = client_other.get("/api/runtime/threads/thread-owned")
                assert resp.status_code == 403

        finally:
            ctx_owner.cleanup()
            ctx_other.cleanup()

    def test_tenant_agent_isolation_in_sync(self, tmp_path):
        """Agents synced by tenant-A are not visible to tenant-B's agent list."""
        paths = _make_paths(tmp_path)
        ctx_a = _IntegrationAppContext(tmp_path / "a", tenant_id="acme", user_id="u1")
        ctx_b = _IntegrationAppContext(tmp_path / "b", tenant_id="globex", user_id="u2")

        try:
            ca = TestClient(ctx_a.app)
            cb = TestClient(ctx_b.app)

            # Tenant A syncs agents
            ca.post("/api/agents/sync", json={
                "agents": [
                    {"name": "acme-bot", "domain": "sales", "soul": "Acme bot."},
                ],
            })

            # Tenant B syncs different agents
            cb.post("/api/agents/sync", json={
                "agents": [
                    {"name": "globex-bot", "domain": "ops", "soul": "Globex bot."},
                ],
            })

            # Tenant A only sees their agent
            a_agents = ca.get("/api/agents").json()["agents"]
            a_names = {a["name"] for a in a_agents}
            assert "acme-bot" in a_names
            assert "globex-bot" not in a_names

            # Tenant B only sees their agent
            b_agents = cb.get("/api/agents").json()["agents"]
            b_names = {a["name"] for a in b_agents}
            assert "globex-bot" in b_names
            assert "acme-bot" not in b_names

        finally:
            ctx_a.cleanup()
            ctx_b.cleanup()


# ── 4. Binding Metadata Lifecycle ────────────────────────────────────────


class TestBindingMetadataLifecycle:
    """Verify binding metadata is correctly managed across the thread lifecycle."""

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_binding_evolves_across_multiple_messages(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        """Binding metadata is updated with each successful message submission."""
        _write_agent_on_disk(integration_ctx.agents_dir, "agent-a", domain="a")
        _write_agent_on_disk(integration_ctx.agents_dir, "agent-b", domain="b")

        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-evolve"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess"})

        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        # First message — uses both agents
        client.post(
            "/api/runtime/threads/thread-evolve/messages:stream",
            json={
                "message": "first message",
                "group_key": "team-v1",
                "allowed_agents": ["agent-a", "agent-b"],
                "entry_agent": "agent-a",
                "requested_orchestration_mode": "auto",
            },
        )

        binding1 = integration_ctx.registry.get_binding("thread-evolve")
        assert binding1["group_key"] == "team-v1"
        assert binding1["allowed_agents"] == ["agent-a", "agent-b"]
        assert binding1["entry_agent"] == "agent-a"
        first_updated = binding1["updated_at"]

        # Second message — narrower allowlist, different orchestration
        client.post(
            "/api/runtime/threads/thread-evolve/messages:stream",
            json={
                "message": "second message",
                "group_key": "team-v2",
                "allowed_agents": ["agent-b"],
                "requested_orchestration_mode": "workflow",
            },
        )

        binding2 = integration_ctx.registry.get_binding("thread-evolve")
        assert binding2["group_key"] == "team-v2"
        assert binding2["allowed_agents"] == ["agent-b"]
        assert binding2["entry_agent"] is None  # not provided this time
        assert binding2["requested_orchestration_mode"] == "workflow"
        assert binding2["updated_at"] != first_updated
        # Original fields preserved
        assert binding2["portal_session_id"] == "sess"
        assert binding2["tenant_id"] == "default"

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_failed_stream_does_not_update_binding(
        self, mock_start, integration_ctx, client
    ):
        """If upstream submission fails, binding metadata remains unchanged."""
        from src.gateway.runtime_service import RuntimeServiceError

        _write_agent_on_disk(integration_ctx.agents_dir, "agent-a", domain="a")

        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-fail"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess_fail"})

        original = integration_ctx.registry.get_binding("thread-fail")

        # Upstream rejects the submission
        mock_start.side_effect = RuntimeServiceError(
            "LangGraph submission failed: Upstream runtime unavailable",
            status_code=503,
        )

        resp = client.post(
            "/api/runtime/threads/thread-fail/messages:stream",
            json={
                "message": "this will fail",
                "group_key": "fail-team",
                "allowed_agents": ["agent-a"],
                "entry_agent": "agent-a",
                "requested_orchestration_mode": "workflow",
            },
        )
        assert resp.status_code == 503

        # Binding must be untouched
        after = integration_ctx.registry.get_binding("thread-fail")
        assert after.get("group_key") is None
        assert after.get("allowed_agents") is None
        assert after.get("entry_agent") is None
        assert after["updated_at"] == original["updated_at"]


# ── 5. SSE Event Contract ────────────────────────────────────────────────


class TestSSEEventContract:
    """Verify the normalized SSE event contract is stable and complete."""

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_stream_response_emits_correct_sse_format(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        """Verify SSE response is well-formed text/event-stream."""
        _write_agent_on_disk(integration_ctx.agents_dir, "bot", domain="test")

        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-sse"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess"})

        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {"thread_id": "thread-sse"}\n\n'
            yield 'event: message_delta\ndata: {"thread_id": "thread-sse", "content": "partial"}\n\n'
            yield 'event: message_completed\ndata: {"thread_id": "thread-sse", "content": "full answer"}\n\n'
            yield 'event: run_completed\ndata: {"thread_id": "thread-sse"}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        resp = client.post(
            "/api/runtime/threads/thread-sse/messages:stream",
            json={
                "message": "test",
                "group_key": "team",
                "allowed_agents": ["bot"],
            },
        )
        assert resp.status_code == 200

        body = resp.text
        # All expected event types present
        assert "event: ack" in body
        assert "event: message_delta" in body
        assert "event: message_completed" in body
        assert "event: run_completed" in body
        # No raw upstream event names leaked
        assert "event: values" not in body
        assert "event: messages/partial" not in body
        assert "event: messages/complete" not in body

    def test_format_sse_produces_valid_sse_frame(self):
        from src.gateway.runtime_service import _format_sse

        frame = _format_sse("test_event", {"key": "value", "num": 42})
        lines = frame.split("\n")
        assert lines[0] == "event: test_event"
        assert lines[1].startswith("data: ")
        payload = json.loads(lines[1].removeprefix("data: "))
        assert payload == {"key": "value", "num": 42}
        assert frame.endswith("\n\n")

    def test_sanitize_error_never_leaks_internal_addresses(self):
        from src.gateway.runtime_service import _sanitize_error

        # Connection errors → generic message
        assert _sanitize_error(ConnectionError("connection refused to 127.0.0.1:2024")) == "Upstream runtime unavailable"
        assert _sanitize_error(TimeoutError("request timed out after 30s")) == "Upstream runtime unavailable"

        # 404 errors → thread not found
        assert _sanitize_error(Exception("404 not found for thread abc")) == "Runtime thread not found"

        # Rejection errors → rejected
        assert _sanitize_error(Exception("409 multitask reject")) == "Runtime rejected the submission"

        # Unknown errors → generic
        result = _sanitize_error(Exception("something unexpected"))
        assert "127.0.0.1" not in result


# ── 6. Thread State Summary Edge Cases ───────────────────────────────────


class TestThreadStateSummary:
    """Test thread state query edge cases."""

    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_get_thread_with_partial_state(self, mock_state, integration_ctx, client):
        """State summary works even when some fields are missing/null."""
        mock_state.return_value = {
            "title": None,
            "run_id": None,
            "workflow_stage": None,
            "workflow_stage_detail": None,
            "artifacts_count": 0,
            "pending_intervention": False,
        }

        integration_ctx.registry.register_binding(
            "thread-partial",
            tenant_id="default",
            user_id="user-1",
            portal_session_id="sess",
        )

        resp = client.get("/api/runtime/threads/thread-partial")
        assert resp.status_code == 200
        state = resp.json()["state"]
        assert state["title"] is None
        assert state["artifacts_count"] == 0
        assert state["pending_intervention"] is False

    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_get_thread_with_rich_state(self, mock_state, integration_ctx, client):
        """State summary returns all fields when fully populated."""
        mock_state.return_value = {
            "title": "Sales Analysis",
            "run_id": "run-42",
            "workflow_stage": "executing",
            "workflow_stage_detail": "Running data-analyst",
            "artifacts_count": 3,
            "pending_intervention": True,
        }

        integration_ctx.registry.register_binding(
            "thread-rich",
            tenant_id="default",
            user_id="user-1",
            portal_session_id="sess",
        )

        resp = client.get("/api/runtime/threads/thread-rich")
        assert resp.status_code == 200
        data = resp.json()
        assert data["portal_session_id"] == "sess"
        state = data["state"]
        assert state["title"] == "Sales Analysis"
        assert state["run_id"] == "run-42"
        assert state["workflow_stage"] == "executing"
        assert state["artifacts_count"] == 3
        assert state["pending_intervention"] is True


# ── 7. Agent Sync + Runtime Interplay Edge Cases ─────────────────────────


class TestAgentSyncRuntimeInterplay:
    """Edge cases at the boundary between agent management and runtime execution."""

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_sync_update_agent_then_stream_uses_updated_config(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        """Agent config changes via sync are reflected in subsequent runtime calls."""
        # Initial sync
        client.post("/api/agents/sync", json={
            "agents": [{"name": "evolving-agent", "domain": "v1", "soul": "Version 1."}],
        })

        # Update via second sync
        client.post("/api/agents/sync", json={
            "agents": [{"name": "evolving-agent", "domain": "v2", "description": "Updated", "soul": "Version 2."}],
        })

        # Verify update took effect
        agent = client.get("/api/agents/evolving-agent").json()
        assert agent["domain"] == "v2"
        assert agent["description"] == "Updated"
        assert agent["soul"] == "Version 2."

        # Create thread and stream — agent must be usable
        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-updated"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess"})

        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        resp = client.post(
            "/api/runtime/threads/thread-updated/messages:stream",
            json={
                "message": "use updated agent",
                "group_key": "team",
                "allowed_agents": ["evolving-agent"],
            },
        )
        assert resp.status_code == 200

    def test_stream_rejects_agent_deleted_after_sync(self, integration_ctx, client):
        """Agent deleted via single DELETE is rejected in subsequent stream."""
        client.post("/api/agents/sync", json={
            "agents": [
                {"name": "temp-agent", "domain": "temp", "soul": "Temp."},
            ],
        })

        # Delete the agent
        del_resp = client.delete("/api/agents/temp-agent")
        assert del_resp.status_code == 204

        # Create thread
        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-deleted-agent"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess"})

        # Stream with deleted agent → 422
        resp = client.post(
            "/api/runtime/threads/thread-deleted-agent/messages:stream",
            json={
                "message": "hi",
                "group_key": "team",
                "allowed_agents": ["temp-agent"],
            },
        )
        assert resp.status_code == 422
        assert "temp-agent" in resp.json()["detail"]

    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_allowed_agents_dedup_and_case_normalization(
        self, mock_iter, mock_start, integration_ctx, client
    ):
        """Duplicate and mixed-case agent names are normalized before context injection."""
        _write_agent_on_disk(integration_ctx.agents_dir, "my-agent", domain="test")

        with patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock) as mc:
            mc.return_value = {"thread_id": "thread-dedup"}
            client.post("/api/runtime/threads", json={"portal_session_id": "sess"})

        mock_start.return_value = ("chunk", None)

        async def fake_events(**kwargs):
            yield 'event: ack\ndata: {}\n\n'

        mock_iter.side_effect = lambda **kwargs: fake_events(**kwargs)

        resp = client.post(
            "/api/runtime/threads/thread-dedup/messages:stream",
            json={
                "message": "test",
                "group_key": "team",
                "allowed_agents": ["My-Agent", "MY-AGENT", "my-agent"],
            },
        )
        assert resp.status_code == 200

        # Context should have deduplicated, lowercase list
        ctx = mock_start.call_args.kwargs["context"]
        assert ctx["allowed_agents"] == ["my-agent"]

        # Binding should also reflect the normalized list
        binding = integration_ctx.registry.get_binding("thread-dedup")
        assert binding["allowed_agents"] == ["my-agent"]
