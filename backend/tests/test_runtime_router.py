"""Tests for Platform Runtime adapter.

Covers:
- ThreadRegistry extended binding API (metadata storage, backward compat)
- Runtime router endpoint validation and behavior
- Payload validation (portal_session_id, message, group_key, allowed_agents, etc.)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.gateway.thread_registry import ThreadRegistry


# ── ThreadRegistry binding extension tests ────────────────────────────


class TestThreadRegistryBindings:
    """Tests for the new register_binding / get_binding / update_binding API."""

    def _make_registry(self, tmp_path: Path) -> ThreadRegistry:
        return ThreadRegistry(registry_file=tmp_path / "thread_registry.json")

    def test_register_binding_creates_metadata(self, tmp_path):
        reg = self._make_registry(tmp_path)
        binding = reg.register_binding(
            "thread-1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        assert binding["tenant_id"] == "tenant-a"
        assert binding["user_id"] == "user-1"
        assert binding["portal_session_id"] == "sess-1"
        assert "created_at" in binding
        assert "updated_at" in binding

    def test_get_binding_returns_metadata(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register_binding(
            "thread-1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        binding = reg.get_binding("thread-1")
        assert binding is not None
        assert binding["tenant_id"] == "tenant-a"
        assert binding["portal_session_id"] == "sess-1"

    def test_get_binding_returns_none_for_unknown(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.get_binding("nonexistent") is None

    def test_update_binding_merges_fields(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register_binding(
            "thread-1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        updated = reg.update_binding(
            "thread-1",
            group_key="team-alpha",
            allowed_agents=["agent-a", "agent-b"],
        )
        assert updated is not None
        assert updated["group_key"] == "team-alpha"
        assert updated["allowed_agents"] == ["agent-a", "agent-b"]
        assert updated["portal_session_id"] == "sess-1"  # preserved
        assert updated["updated_at"] != updated.get("created_at")

    def test_update_binding_returns_none_for_unknown(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.update_binding("nonexistent", group_key="x") is None

    def test_backward_compat_old_string_format(self, tmp_path):
        """Old-format string entries are transparently promoted."""
        file = tmp_path / "thread_registry.json"
        file.write_text(json.dumps({"thread-old": "tenant-legacy"}), encoding="utf-8")

        reg = ThreadRegistry(registry_file=file)
        assert reg.get_tenant("thread-old") == "tenant-legacy"
        assert reg.check_access("thread-old", "tenant-legacy") is True
        assert reg.check_access("thread-old", "other") is False

        binding = reg.get_binding("thread-old")
        assert binding is not None
        assert binding["tenant_id"] == "tenant-legacy"

    def test_register_preserves_existing_metadata(self, tmp_path):
        """Calling register() on a thread with binding metadata preserves other fields."""
        reg = self._make_registry(tmp_path)
        reg.register_binding(
            "thread-1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        # Old-style register call
        reg.register("thread-1", "tenant-b")
        binding = reg.get_binding("thread-1")
        assert binding["tenant_id"] == "tenant-b"
        assert binding["portal_session_id"] == "sess-1"  # preserved

    def test_list_threads_with_metadata_entries(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register_binding("thread-1", tenant_id="tenant-a", user_id="u1", portal_session_id="s1")
        reg.register_binding("thread-2", tenant_id="tenant-b", user_id="u2", portal_session_id="s2")
        reg.register_binding("thread-3", tenant_id="tenant-a", user_id="u3", portal_session_id="s3")
        assert sorted(reg.list_threads("tenant-a")) == ["thread-1", "thread-3"]

    def test_register_binding_with_optional_fields(self, tmp_path):
        reg = self._make_registry(tmp_path)
        binding = reg.register_binding(
            "thread-1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
            group_key="team-x",
            allowed_agents=["agent-a"],
            entry_agent="agent-a",
            requested_orchestration_mode="workflow",
        )
        assert binding["group_key"] == "team-x"
        assert binding["allowed_agents"] == ["agent-a"]
        assert binding["entry_agent"] == "agent-a"
        assert binding["requested_orchestration_mode"] == "workflow"

    def test_persistence_across_instances(self, tmp_path):
        file = tmp_path / "thread_registry.json"
        reg1 = ThreadRegistry(registry_file=file)
        reg1.register_binding("thread-1", tenant_id="t", user_id="u", portal_session_id="s")

        reg2 = ThreadRegistry(registry_file=file)
        binding = reg2.get_binding("thread-1")
        assert binding is not None
        assert binding["tenant_id"] == "t"


# ── Runtime router tests ──────────────────────────────────────────────


class _TestAppContext:
    """Manages a test app with monkey-patched module globals, restoring on cleanup."""

    def __init__(
        self,
        tmp_path: Path,
        agents_dir: Path | None = None,
        *,
        tenant_id: str = "default",
        user_id: str = "user-1",
        username: str = "tester",
    ):
        from fastapi import FastAPI

        from src.gateway.routers import runtime

        self._runtime_mod = runtime
        self._original_get_registry = runtime.get_thread_registry
        self._original_resolve_agents_dir = runtime._resolve_agents_dir

        self.registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        self.app = FastAPI()
        self.app.include_router(runtime.router)

        @self.app.middleware("http")
        async def inject_identity(request, call_next):
            request.state.tenant_id = tenant_id
            request.state.user_id = user_id
            request.state.username = username
            return await call_next(request)

        _registry = self.registry
        runtime.get_thread_registry = lambda: _registry  # type: ignore

        if agents_dir is not None:
            runtime._resolve_agents_dir = lambda tenant_id: agents_dir  # type: ignore

    def cleanup(self):
        self._runtime_mod.get_thread_registry = self._original_get_registry  # type: ignore
        self._runtime_mod._resolve_agents_dir = self._original_resolve_agents_dir  # type: ignore


def _create_test_app(
    tmp_path: Path,
    agents_dir: Path | None = None,
    *,
    tenant_id: str = "default",
    user_id: str = "user-1",
    username: str = "tester",
):
    """Create a minimal FastAPI app with the runtime router for testing.

    IMPORTANT: Call ctx.cleanup() after the test (or use it within a fixture).
    Returns (app, registry, ctx).
    """
    ctx = _TestAppContext(
        tmp_path,
        agents_dir,
        tenant_id=tenant_id,
        user_id=user_id,
        username=username,
    )
    return ctx.app, ctx.registry, ctx


class TestRuntimeThreadCreation:
    """POST /api/runtime/threads"""

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_create_thread_success(self, mock_create, tmp_path):
        mock_create.return_value = {"thread_id": "thread-abc"}
        app, registry, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads",
                json={"portal_session_id": "sess_123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == "thread-abc"
            assert data["portal_session_id"] == "sess_123"
            assert data["tenant_id"] == "default"
            assert data["user_id"] == "user-1"
            assert "created_at" in data

            # Verify registry was updated
            binding = registry.get_binding("thread-abc")
            assert binding is not None
            assert binding["portal_session_id"] == "sess_123"
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_create_thread_empty_session_id(self, mock_create, tmp_path):
        app, _, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads",
                json={"portal_session_id": "   "},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_create_thread_session_id_too_long(self, mock_create, tmp_path):
        app, _, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads",
                json={"portal_session_id": "x" * 129},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_create_thread_upstream_failure(self, mock_create, tmp_path):
        from src.gateway.runtime_service import RuntimeServiceError

        mock_create.side_effect = RuntimeServiceError("upstream down")
        app, _, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads",
                json={"portal_session_id": "sess_123"},
            )
            assert resp.status_code == 503
        finally:
            ctx.cleanup()


class TestRuntimeThreadGet:
    """GET /api/runtime/threads/{thread_id}"""

    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_get_thread_success(self, mock_state, tmp_path):
        mock_state.return_value = {
            "title": "Test",
            "run_id": "run-1",
            "workflow_stage": None,
            "workflow_stage_detail": None,
            "artifacts_count": 0,
            "pending_intervention": False,
        }

        app, registry, ctx = _create_test_app(tmp_path)
        try:
            registry.register_binding(
                "thread-1",
                tenant_id="default",
                user_id="user-1",
                portal_session_id="sess-1",
            )

            client = TestClient(app)
            resp = client.get("/api/runtime/threads/thread-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == "thread-1"
            assert data["portal_session_id"] == "sess-1"
            assert data["state"]["title"] == "Test"
        finally:
            ctx.cleanup()

    def test_get_thread_not_found(self, tmp_path):
        app, _, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.get("/api/runtime/threads/nonexistent")
            assert resp.status_code == 404
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_get_thread_cross_tenant_denied(self, mock_state, tmp_path):
        app, registry, ctx = _create_test_app(tmp_path)
        try:
            registry.register_binding(
                "thread-1",
                tenant_id="tenant-other",
                user_id="user-1",
                portal_session_id="sess-1",
            )

            client = TestClient(app)
            # Default tenant_id is "default", thread belongs to "tenant-other"
            resp = client.get("/api/runtime/threads/thread-1")
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_get_thread_cross_owner_denied(self, mock_state, tmp_path):
        app, registry, ctx = _create_test_app(tmp_path, user_id="user-2")
        try:
            registry.register_binding(
                "thread-1",
                tenant_id="default",
                user_id="user-1",
                portal_session_id="sess-1",
            )

            client = TestClient(app)
            resp = client.get("/api/runtime/threads/thread-1")
            assert resp.status_code == 403
        finally:
            ctx.cleanup()


class TestRuntimeMessageStream:
    """POST /api/runtime/threads/{thread_id}/messages:stream"""

    def _setup(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for name in ("research-agent", "data-analyst"):
            d = agents_dir / name
            d.mkdir()
            (d / "config.yaml").write_text(f"name: {name}\ndescription: test {name}", encoding="utf-8")

        app, registry, ctx = _create_test_app(tmp_path, agents_dir=agents_dir)
        registry.register_binding(
            "thread-1",
            tenant_id="default",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        return app, registry, ctx

    @patch("src.gateway.routers.runtime.stream_message")
    def test_stream_success(self, mock_stream, tmp_path):
        async def fake_stream(**kwargs):
            kwargs["on_submit_success"]()
            yield 'event: ack\ndata: {"thread_id": "thread-1"}\n\n'
            yield 'event: run_completed\ndata: {"thread_id": "thread-1"}\n\n'

        mock_stream.side_effect = lambda **kwargs: fake_stream(**kwargs)

        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team-alpha",
                    "allowed_agents": ["research-agent", "data-analyst"],
                },
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            # Verify binding was updated
            binding = registry.get_binding("thread-1")
            assert binding["group_key"] == "team-alpha"
            assert binding["allowed_agents"] == ["research-agent", "data-analyst"]
        finally:
            ctx.cleanup()

    def test_stream_thread_not_found(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        app, _, ctx = _create_test_app(tmp_path, agents_dir=agents_dir)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/nonexistent/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["agent-a"],
                },
            )
            assert resp.status_code == 404
        finally:
            ctx.cleanup()

    def test_stream_empty_message(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "   ",
                    "group_key": "team",
                    "allowed_agents": ["research-agent"],
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_stream_empty_group_key(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "",
                    "allowed_agents": ["research-agent"],
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_stream_empty_allowed_agents(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": [],
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_stream_unknown_agent(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["nonexistent-agent"],
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_stream_entry_agent_not_in_allowed(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent"],
                    "entry_agent": "data-analyst",
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.stream_message")
    def test_stream_entry_agent_in_allowed(self, mock_stream, tmp_path):
        async def fake_stream(**kwargs):
            kwargs["on_submit_success"]()
            yield 'event: ack\ndata: {}\n\n'

        mock_stream.side_effect = lambda **kwargs: fake_stream(**kwargs)

        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent", "data-analyst"],
                    "entry_agent": "research-agent",
                },
            )
            assert resp.status_code == 200

            binding = registry.get_binding("thread-1")
            assert binding["entry_agent"] == "research-agent"
        finally:
            ctx.cleanup()

    def test_stream_invalid_orchestration_mode(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent"],
                    "requested_orchestration_mode": "invalid_mode",
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_stream_metadata_non_primitive_rejected(self, tmp_path):
        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent"],
                    "metadata": {"nested": {"key": "value"}},
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.stream_message")
    def test_stream_metadata_primitive_accepted(self, mock_stream, tmp_path):
        async def fake_stream(**kwargs):
            kwargs["on_submit_success"]()
            yield 'event: ack\ndata: {}\n\n'

        mock_stream.side_effect = lambda **kwargs: fake_stream(**kwargs)

        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent"],
                    "metadata": {"source": "portal", "count": 5, "debug": True, "extra": None},
                },
            )
            assert resp.status_code == 200
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.stream_message")
    def test_stream_deduplicates_allowed_agents(self, mock_stream, tmp_path):
        async def fake_stream(**kwargs):
            kwargs["on_submit_success"]()
            yield 'event: ack\ndata: {}\n\n'

        mock_stream.side_effect = lambda **kwargs: fake_stream(**kwargs)

        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent", "Research-Agent", "data-analyst"],
                },
            )
            assert resp.status_code == 200

            binding = registry.get_binding("thread-1")
            # Should be deduplicated to lowercase
            assert binding["allowed_agents"] == ["research-agent", "data-analyst"]
        finally:
            ctx.cleanup()

    def test_stream_cross_tenant_denied(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        app, registry, ctx = _create_test_app(tmp_path, agents_dir=agents_dir)
        try:
            registry.register_binding(
                "thread-1",
                tenant_id="tenant-other",
                user_id="user-1",
                portal_session_id="sess-1",
            )

            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["agent-a"],
                },
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    def test_stream_cross_owner_denied(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "research-agent").mkdir()
        app, registry, ctx = _create_test_app(tmp_path, agents_dir=agents_dir, user_id="user-2")
        try:
            registry.register_binding(
                "thread-1",
                tenant_id="default",
                user_id="user-1",
                portal_session_id="sess-1",
            )

            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team",
                    "allowed_agents": ["research-agent"],
                },
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.stream_message")
    def test_stream_failed_submission_does_not_persist_binding_metadata(self, mock_stream, tmp_path):
        async def fake_stream(**kwargs):
            yield 'event: ack\ndata: {"thread_id": "thread-1"}\n\n'
            yield 'event: run_failed\ndata: {"thread_id": "thread-1", "error": "Upstream runtime unavailable"}\n\n'

        mock_stream.return_value = fake_stream()

        app, registry, ctx = self._setup(tmp_path)
        try:
            original_updated_at = registry.get_binding("thread-1")["updated_at"]
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team-alpha",
                    "allowed_agents": ["research-agent"],
                    "entry_agent": "research-agent",
                    "requested_orchestration_mode": "workflow",
                },
            )
            assert resp.status_code == 200

            binding = registry.get_binding("thread-1")
            assert binding.get("group_key") is None
            assert binding.get("allowed_agents") is None
            assert binding.get("entry_agent") is None
            assert binding.get("requested_orchestration_mode") is None
            assert binding["updated_at"] == original_updated_at
        finally:
            ctx.cleanup()


class TestRuntimeServiceStateSummary:
    """Tests for runtime_service thread state error mapping."""

    @patch("src.gateway.runtime_service._get_client")
    def test_get_thread_state_summary_maps_not_found_to_404(self, mock_get_client):
        from src.gateway.runtime_service import RuntimeServiceError, get_thread_state_summary

        client = MagicMock()
        client.threads.get_state = AsyncMock(side_effect=Exception("404 not found"))
        mock_get_client.return_value = client

        with pytest.raises(RuntimeServiceError) as exc_info:
            asyncio.run(get_thread_state_summary("thread-404"))

        assert exc_info.value.status_code == 404
        assert "thread-404" in str(exc_info.value)

    @patch("src.gateway.runtime_service._get_client")
    def test_get_thread_state_summary_maps_connectivity_errors_to_503(self, mock_get_client):
        from src.gateway.runtime_service import RuntimeServiceError, get_thread_state_summary

        client = MagicMock()
        client.threads.get_state = AsyncMock(side_effect=ConnectionError("connection refused"))
        mock_get_client.return_value = client

        with pytest.raises(RuntimeServiceError) as exc_info:
            asyncio.run(get_thread_state_summary("thread-503"))

        assert exc_info.value.status_code == 503
        assert "LangGraph unavailable" in str(exc_info.value)


class TestRuntimeServiceSSE:
    """Tests for SSE event normalization in runtime_service."""

    def test_format_sse(self):
        from src.gateway.runtime_service import _format_sse

        result = _format_sse("ack", {"thread_id": "t1"})
        assert result.startswith("event: ack\n")
        assert '"thread_id": "t1"' in result
        assert result.endswith("\n\n")

    def test_normalize_unknown_event_skipped(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "unknown_event_type"
        chunk.data = {"foo": "bar"}
        results = _normalize_stream_event(chunk, "t1", None)
        assert results == []

    def test_normalize_values_with_ai_message(self):
        from src.gateway.runtime_service import SSE_MESSAGE_COMPLETED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "messages": [{"type": "ai", "content": "Hello world"}],
        }
        results = _normalize_stream_event(chunk, "t1", "run-1")
        assert len(results) >= 1
        event_name, payload = results[0]
        assert event_name == SSE_MESSAGE_COMPLETED
        assert payload["content"] == "Hello world"

    def test_normalize_values_deduplicates_same_ai_message(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "messages": [{"type": "ai", "content": "Hello world"}],
        }
        # Second call with same content should produce nothing
        results = _normalize_stream_event(chunk, "t1", "run-1", _last_ai_content="Hello world")
        message_events = [(n, p) for n, p in results if n == "message_completed"]
        assert len(message_events) == 0

    def test_normalize_values_intervention_from_task_pool(self):
        from src.gateway.runtime_service import SSE_INTERVENTION_REQUESTED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "messages": [],
            "task_pool": [
                {
                    "status": "WAITING_INTERVENTION",
                    "intervention_status": "pending",
                    "intervention_request": {
                        "request_id": "req-1",
                        "type": "clarification",
                    },
                }
            ],
        }
        results = _normalize_stream_event(chunk, "t1", "run-1")
        intv_events = [(n, p) for n, p in results if n == SSE_INTERVENTION_REQUESTED]
        assert len(intv_events) == 1
        assert intv_events[0][1]["request_id"] == "req-1"

    def test_normalize_values_intervention_includes_fingerprint(self):
        from src.gateway.runtime_service import SSE_INTERVENTION_REQUESTED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "task_pool": [
                {
                    "status": "WAITING_INTERVENTION",
                    "intervention_status": "pending",
                    "intervention_request": {
                        "request_id": "req-1",
                        "type": "clarification",
                        "fingerprint": "fp-123",
                    },
                }
            ],
        }

        results = _normalize_stream_event(chunk, "t1", "run-1")
        intv_events = [(n, p) for n, p in results if n == SSE_INTERVENTION_REQUESTED]
        assert len(intv_events) == 1
        assert intv_events[0][1]["fingerprint"] == "fp-123"

    def test_normalize_values_artifact_includes_top_level_artifact_url(self):
        from src.gateway.runtime_service import SSE_ARTIFACT_CREATED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "artifacts": [
                {
                    "name": "report",
                    "artifact_url": "/api/threads/t1/artifacts/report",
                }
            ],
        }

        results = _normalize_stream_event(chunk, "t1", "run-1")
        artifact_events = [(n, p) for n, p in results if n == SSE_ARTIFACT_CREATED]
        assert len(artifact_events) == 1
        assert artifact_events[0][1]["artifact_url"] == "/api/threads/t1/artifacts/report"

    def test_normalize_messages_partial_delta(self):
        from src.gateway.runtime_service import SSE_MESSAGE_DELTA, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "messages/partial"
        chunk.data = (
            {"type": "AIMessageChunk", "content": "partial"},
            {"run_id": "run-x"},
        )
        results = _normalize_stream_event(chunk, "t1", None)
        assert len(results) == 1
        event_name, payload = results[0]
        assert event_name == SSE_MESSAGE_DELTA
        assert payload["content"] == "partial"
        assert payload["run_id"] == "run-x"

    def test_normalize_messages_complete(self):
        from src.gateway.runtime_service import SSE_MESSAGE_COMPLETED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "messages/complete"
        chunk.data = (
            {"type": "ai", "content": "Final answer"},
            {"run_id": "run-y"},
        )
        results = _normalize_stream_event(chunk, "t1", None)
        assert len(results) == 1
        event_name, payload = results[0]
        assert event_name == SSE_MESSAGE_COMPLETED
        assert payload["content"] == "Final answer"

    @patch("src.gateway.runtime_service._get_client")
    def test_stream_message_emits_stable_run_failed_error_text(self, mock_get_client):
        from src.gateway.runtime_service import stream_message

        class _FailingRuns:
            async def stream(self, *args, **kwargs):
                raise ConnectionError("connection refused to 127.0.0.1:2024")
                yield

        client = MagicMock()
        client.runs = _FailingRuns()
        mock_get_client.return_value = client

        async def _collect():
            return [
                chunk
                async for chunk in stream_message(
                    thread_id="t1",
                    message="hello",
                    context={"thread_id": "t1"},
                )
            ]

        frames = asyncio.run(_collect())
        assert any("event: run_failed" in frame for frame in frames)
        failed_frame = next(frame for frame in frames if "event: run_failed" in frame)
        assert "Upstream runtime unavailable" in failed_frame
        assert "127.0.0.1:2024" not in failed_frame
