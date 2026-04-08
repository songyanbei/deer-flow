"""Comprehensive review test suite for Platform-DeerFlow Runtime Integration.

This test file covers edge cases, security scenarios, concurrency, and
robustness testing that go beyond the happy-path tests in the existing suite.

Organized by component:
1. ThreadRegistry — edge cases, corruption, concurrency
2. Runtime Router — boundary validation, injection, access control
3. Runtime Service — SSE normalization edge cases, error mapping
4. Batch Sync — edge cases, partial failures, idempotency
5. allowed_agents integration — end-to-end contract verification
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.thread_registry import ThreadRegistry


# ═══════════════════════════════════════════════════════════════════════
# 1. ThreadRegistry — Edge Cases, Corruption, Concurrency
# ═══════════════════════════════════════════════════════════════════════


class TestThreadRegistryEdgeCases:
    """Edge cases and robustness tests for ThreadRegistry."""

    def _make(self, tmp_path: Path) -> ThreadRegistry:
        return ThreadRegistry(registry_file=tmp_path / "thread_registry.json")

    # ── Corrupted / malformed file ──

    def test_corrupted_json_file_does_not_crash(self, tmp_path):
        """Registry should gracefully handle corrupted JSON on disk."""
        file = tmp_path / "thread_registry.json"
        file.write_text("{invalid json!!!", encoding="utf-8")
        reg = ThreadRegistry(registry_file=file)
        assert reg.get_binding("any") is None
        assert reg.get_tenant("any") is None
        assert reg.list_threads("default") == []

    def test_non_dict_json_treated_as_empty(self, tmp_path):
        """If the JSON file contains a non-dict (e.g., array), treat as empty."""
        file = tmp_path / "thread_registry.json"
        file.write_text('[1, 2, 3]', encoding="utf-8")
        reg = ThreadRegistry(registry_file=file)
        assert reg.get_binding("any") is None

    def test_empty_file_treated_as_empty(self, tmp_path):
        """Empty file should be handled gracefully."""
        file = tmp_path / "thread_registry.json"
        file.write_text("", encoding="utf-8")
        reg = ThreadRegistry(registry_file=file)
        assert reg.get_binding("any") is None

    # ── ID validation ──

    def test_register_binding_rejects_path_traversal(self, tmp_path):
        """Thread IDs with path traversal characters must be rejected."""
        reg = self._make(tmp_path)
        with pytest.raises(ValueError, match="Invalid thread_id"):
            reg.register_binding(
                "../../../etc/passwd",
                tenant_id="t", user_id="u", portal_session_id="s",
            )

    def test_register_binding_rejects_spaces(self, tmp_path):
        reg = self._make(tmp_path)
        with pytest.raises(ValueError, match="Invalid thread_id"):
            reg.register_binding(
                "thread with spaces",
                tenant_id="t", user_id="u", portal_session_id="s",
            )

    def test_register_binding_rejects_dots(self, tmp_path):
        reg = self._make(tmp_path)
        with pytest.raises(ValueError, match="Invalid thread_id"):
            reg.register_binding(
                "thread.id.with.dots",
                tenant_id="t", user_id="u", portal_session_id="s",
            )

    def test_register_rejects_invalid_id(self, tmp_path):
        """The original register() method should also validate IDs."""
        reg = self._make(tmp_path)
        with pytest.raises(ValueError, match="Invalid thread_id"):
            reg.register("../attack", "tenant-a")

    # ── update_binding with arbitrary fields ──

    def test_update_binding_rejects_arbitrary_keys(self, tmp_path):
        """update_binding enforces a whitelist — unexpected keys are rejected."""
        reg = self._make(tmp_path)
        reg.register_binding("thread-1", tenant_id="t", user_id="u", portal_session_id="s")
        with pytest.raises(ValueError, match="Cannot update protected binding field"):
            reg.update_binding("thread-1", unexpected_field="surprise")

    def test_update_binding_cannot_overwrite_tenant_id(self, tmp_path):
        """update_binding prevents overwriting identity fields like tenant_id."""
        reg = self._make(tmp_path)
        reg.register_binding("thread-1", tenant_id="tenant-a", user_id="u", portal_session_id="s")
        with pytest.raises(ValueError, match="Cannot update protected binding field"):
            reg.update_binding("thread-1", tenant_id="tenant-hijacked")
        # Verify original value is preserved
        binding = reg.get_binding("thread-1")
        assert binding["tenant_id"] == "tenant-a"

    # ── Cache invalidation ──

    def test_invalidate_cache_forces_reload(self, tmp_path):
        file = tmp_path / "thread_registry.json"
        reg = ThreadRegistry(registry_file=file)
        reg.register_binding("thread-1", tenant_id="t", user_id="u", portal_session_id="s")

        # Externally modify the file
        data = json.loads(file.read_text(encoding="utf-8"))
        data["thread-external"] = {"tenant_id": "ext"}
        file.write_text(json.dumps(data), encoding="utf-8")

        # Before invalidation, cache is stale
        assert reg.get_binding("thread-external") is None

        reg.invalidate_cache()
        binding = reg.get_binding("thread-external")
        assert binding is not None
        assert binding["tenant_id"] == "ext"

    # ── Concurrency ──

    def test_concurrent_register_binding(self, tmp_path):
        """Multiple threads registering concurrently should not corrupt the file."""
        reg = self._make(tmp_path)
        errors = []

        def register_thread(i):
            try:
                reg.register_binding(
                    f"thread-{i}",
                    tenant_id="t",
                    user_id=f"u-{i}",
                    portal_session_id=f"s-{i}",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_thread, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent errors: {errors}"

        # All 20 threads should be registered
        for i in range(20):
            binding = reg.get_binding(f"thread-{i}")
            assert binding is not None, f"thread-{i} missing"

    # ── Atomic write safety ──

    def test_register_binding_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "deep" / "nested" / "thread_registry.json"
        reg = ThreadRegistry(registry_file=deep_path)
        binding = reg.register_binding("thread-1", tenant_id="t", user_id="u", portal_session_id="s")
        assert binding is not None
        assert deep_path.exists()

    # ── Unregister ──

    def test_unregister_nonexistent_returns_false(self, tmp_path):
        reg = self._make(tmp_path)
        assert reg.unregister("nonexistent") is False

    def test_unregister_removes_binding(self, tmp_path):
        reg = self._make(tmp_path)
        reg.register_binding("thread-1", tenant_id="t", user_id="u", portal_session_id="s")
        assert reg.unregister("thread-1") is True
        assert reg.get_binding("thread-1") is None


# ═══════════════════════════════════════════════════════════════════════
# 2. Runtime Router — Boundary Validation, Injection, Access Control
# ═══════════════════════════════════════════════════════════════════════


def _make_runtime_app(
    tmp_path: Path,
    agents_dir: Path | None = None,
    *,
    tenant_id: str = "default",
    user_id: str = "user-1",
    username: str = "tester",
):
    """Create a minimal FastAPI app with runtime router for testing."""
    from src.gateway.routers import runtime
    from src.gateway import thread_registry as _tr_mod

    original_registry = runtime.get_thread_registry
    original_resolve = runtime._resolve_agents_dir
    original_tr_get_registry = _tr_mod.get_thread_registry

    registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
    app = FastAPI()
    app.include_router(runtime.router)

    @app.middleware("http")
    async def inject_identity(request, call_next):
        request.state.tenant_id = tenant_id
        request.state.user_id = user_id
        request.state.username = username
        request.state.role = "admin"
        return await call_next(request)

    runtime.get_thread_registry = lambda: registry
    _tr_mod.get_thread_registry = lambda: registry
    if agents_dir is not None:
        runtime._resolve_agents_dir = lambda tid: agents_dir

    def cleanup():
        runtime.get_thread_registry = original_registry
        runtime._resolve_agents_dir = original_resolve
        _tr_mod.get_thread_registry = original_tr_get_registry

    return app, registry, cleanup


class TestRuntimeRouterBoundaryValidation:
    """Test boundary conditions and injection attempts on runtime endpoints."""

    def _setup_agents(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for name in ("agent-a", "agent-b"):
            d = agents_dir / name
            d.mkdir()
            (d / "config.yaml").write_text(f"name: {name}\ndescription: test", encoding="utf-8")
        return agents_dir

    # ── Message field extremes ──

    def test_message_whitespace_only_rejected(self, tmp_path):
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "\t\n  ", "group_key": "g", "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 422
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_message_very_long_accepted(self, mock_start, mock_iter, tmp_path):
        """No max length on message — verify it doesn't crash with large input."""
        mock_start.return_value = (None, None)
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "x" * 100_000, "group_key": "g", "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 200
        finally:
            cleanup()

    # ── allowed_agents edge cases ──

    def test_allowed_agents_all_whitespace_names(self, tmp_path):
        """Agent names that are all whitespace should be rejected."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hi", "group_key": "g", "allowed_agents": ["  ", "agent-a"]},
            )
            assert resp.status_code == 422
            assert "empty agent name" in resp.json()["detail"].lower()
        finally:
            cleanup()

    def test_allowed_agents_single_valid_agent(self, tmp_path):
        """Single agent in allowlist should work fine."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            with patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock) as mock_start, \
                 patch("src.gateway.routers.runtime.iter_events") as mock_iter:
                mock_start.return_value = (None, None)
                async def fake_iter(**kw):
                    yield 'event: ack\ndata: {}\n\n'
                mock_iter.side_effect = lambda **kw: fake_iter(**kw)
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/messages:stream",
                    json={"message": "hi", "group_key": "g", "allowed_agents": ["agent-a"]},
                )
                assert resp.status_code == 200
        finally:
            cleanup()

    def test_entry_agent_empty_string_rejected(self, tmp_path):
        """Empty string entry_agent should be rejected."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hi", "group_key": "g",
                    "allowed_agents": ["agent-a"],
                    "entry_agent": "   ",
                },
            )
            assert resp.status_code == 422
        finally:
            cleanup()

    # ── metadata edge cases ──

    def test_metadata_with_list_value_rejected(self, tmp_path):
        """Lists in metadata should be rejected (not primitive)."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hi", "group_key": "g",
                    "allowed_agents": ["agent-a"],
                    "metadata": {"tags": ["a", "b"]},
                },
            )
            assert resp.status_code == 422
        finally:
            cleanup()

    def test_metadata_with_empty_dict_accepted(self, tmp_path):
        """Empty metadata dict should be accepted."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            with patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock) as mock_start, \
                 patch("src.gateway.routers.runtime.iter_events") as mock_iter:
                mock_start.return_value = (None, None)
                async def fake_iter(**kw):
                    yield 'event: ack\ndata: {}\n\n'
                mock_iter.side_effect = lambda **kw: fake_iter(**kw)
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/messages:stream",
                    json={
                        "message": "hi", "group_key": "g",
                        "allowed_agents": ["agent-a"],
                        "metadata": {},
                    },
                )
                assert resp.status_code == 200
        finally:
            cleanup()

    # ── portal_session_id edge cases ──

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_portal_session_id_exactly_128_chars(self, mock_create, tmp_path):
        """128 chars should be accepted (boundary)."""
        mock_create.return_value = {"thread_id": "thread-ok"}
        app, reg, cleanup = _make_runtime_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post("/api/runtime/threads", json={"portal_session_id": "x" * 128})
            assert resp.status_code == 201
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_portal_session_id_129_chars_rejected(self, mock_create, tmp_path):
        """129 chars should be rejected."""
        app, _, cleanup = _make_runtime_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post("/api/runtime/threads", json={"portal_session_id": "x" * 129})
            assert resp.status_code == 422
        finally:
            cleanup()

    # ── Context verification ──

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_context_includes_all_required_fields(self, mock_start, mock_iter, tmp_path):
        """Verify the context dict passed to start_stream has all expected keys."""
        captured_context = {}

        async def fake_start(*, thread_id, message, context):
            captured_context.update(context)
            return (None, None)

        mock_start.side_effect = fake_start
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team-x",
                    "allowed_agents": ["agent-a", "agent-b"],
                    "entry_agent": "agent-a",
                    "requested_orchestration_mode": "workflow",
                },
            )
            assert resp.status_code == 200
            assert captured_context["thread_id"] == "thread-1"
            assert captured_context["tenant_id"] == "default"
            assert captured_context["user_id"] == "user-1"
            assert captured_context["username"] == "tester"
            assert captured_context["allowed_agents"] == ["agent-a", "agent-b"]
            assert captured_context["group_key"] == "team-x"
            assert captured_context["requested_orchestration_mode"] == "workflow"
            assert captured_context["agent_name"] == "agent-a"
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_context_omits_agent_name_when_no_entry_agent(self, mock_start, mock_iter, tmp_path):
        """When entry_agent is not provided, agent_name should NOT be in context."""
        captured_context = {}

        async def fake_start(*, thread_id, message, context):
            captured_context.update(context)
            return (None, None)

        mock_start.side_effect = fake_start
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hello", "group_key": "team", "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 200
            assert "agent_name" not in captured_context
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_context_omits_orchestration_mode_when_not_provided(self, mock_start, mock_iter, tmp_path):
        captured_context = {}

        async def fake_start(*, thread_id, message, context):
            captured_context.update(context)
            return (None, None)

        mock_start.side_effect = fake_start
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hello", "group_key": "team", "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 200
            assert "requested_orchestration_mode" not in captured_context
        finally:
            cleanup()

    # ── metadata NOT passed to context ──

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_metadata_not_passed_to_runtime_context(self, mock_start, mock_iter, tmp_path):
        """metadata is validated but not injected into the LangGraph runtime context."""
        captured_context = {}

        async def fake_start(*, thread_id, message, context):
            captured_context.update(context)
            return (None, None)

        mock_start.side_effect = fake_start
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello", "group_key": "team",
                    "allowed_agents": ["agent-a"],
                    "metadata": {"source": "portal", "version": 2},
                },
            )
            assert resp.status_code == 200
            # metadata is validated but NOT included in runtime context
            assert "metadata" not in captured_context
        finally:
            cleanup()


class TestRuntimeRouterAccessControl:
    """Comprehensive access control scenarios."""

    def test_get_thread_by_same_tenant_different_user_denied(self, tmp_path):
        """Same tenant but different user should be denied."""
        app, reg, cleanup = _make_runtime_app(tmp_path, user_id="user-2")
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.get("/api/runtime/threads/thread-1")
            assert resp.status_code == 403
        finally:
            cleanup()

    def test_get_thread_by_different_tenant_same_user_denied(self, tmp_path):
        """Different tenant even with same user_id should be denied."""
        app, reg, cleanup = _make_runtime_app(tmp_path, tenant_id="tenant-b")
        try:
            reg.register_binding("thread-1", tenant_id="tenant-a", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            # Note: app injects tenant_id="tenant-b" but thread belongs to "tenant-a"
            resp = client.get("/api/runtime/threads/thread-1")
            assert resp.status_code == 403
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_get_thread_binding_without_user_id_allows_same_tenant(self, mock_state, tmp_path):
        """Old-format binding (no user_id) should allow access when tenant matches."""
        mock_state.return_value = {
            "title": None, "run_id": None, "workflow_stage": None,
            "workflow_stage_detail": None, "artifacts_count": 0, "pending_intervention": False,
        }
        app, reg, cleanup = _make_runtime_app(tmp_path)
        try:
            # Simulate old-format: register with only tenant_id
            reg.register("thread-old", "default")
            client = TestClient(app)
            resp = client.get("/api/runtime/threads/thread-old")
            assert resp.status_code == 200
        finally:
            cleanup()


# ═══════════════════════════════════════════════════════════════════════
# 3. Runtime Service — SSE Edge Cases, Error Mapping
# ═══════════════════════════════════════════════════════════════════════


class TestRuntimeServiceSSEEdgeCases:
    """Edge cases in SSE event normalization."""

    def test_normalize_values_empty_messages_list(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {"messages": []}
        results = _normalize_stream_event(chunk, "t1", "run-1")
        message_events = [r for r in results if r[0] in ("message_completed", "message_delta")]
        assert len(message_events) == 0

    def test_normalize_values_non_ai_message_ignored(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {"messages": [{"type": "human", "content": "User said hello"}]}
        results = _normalize_stream_event(chunk, "t1", "run-1")
        message_events = [r for r in results if r[0] == "message_completed"]
        assert len(message_events) == 0

    def test_normalize_values_ai_message_empty_content_ignored(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {"messages": [{"type": "ai", "content": ""}]}
        results = _normalize_stream_event(chunk, "t1", "run-1")
        message_events = [r for r in results if r[0] == "message_completed"]
        assert len(message_events) == 0

    def test_normalize_values_none_data_returns_empty(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = None
        results = _normalize_stream_event(chunk, "t1", "run-1")
        assert results == []

    def test_normalize_values_string_data_returns_empty(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = "just a string"
        results = _normalize_stream_event(chunk, "t1", "run-1")
        assert results == []

    def test_normalize_messages_partial_non_ai_ignored(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "messages/partial"
        chunk.data = ({"type": "ToolMessage", "content": "tool output"}, {})
        results = _normalize_stream_event(chunk, "t1", "run-1")
        assert results == []

    def test_normalize_messages_complete_non_ai_ignored(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "messages/complete"
        chunk.data = ({"type": "tool", "content": "tool result"}, {})
        results = _normalize_stream_event(chunk, "t1", "run-1")
        assert results == []

    def test_normalize_values_intervention_not_re_emitted_by_request_id(self):
        """An intervention with the same request_id should not be re-emitted."""
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "task_pool": [{
                "status": "WAITING_INTERVENTION",
                "intervention_status": "pending",
                "intervention_request": {"request_id": "r1", "type": "approve"},
            }],
        }
        # r1 already emitted
        results = _normalize_stream_event(
            chunk, "t1", "run-1", _emitted_intervention_ids={"r1"}
        )
        intv = [r for r in results if r[0] == "intervention_requested"]
        assert len(intv) == 0

    def test_normalize_values_new_intervention_emitted_after_previous(self):
        """A new intervention with a different request_id should be emitted."""
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "task_pool": [
                {
                    "status": "WAITING_INTERVENTION",
                    "intervention_status": "pending",
                    "intervention_request": {"request_id": "r1", "type": "approve"},
                },
                {
                    "status": "WAITING_INTERVENTION",
                    "intervention_status": "pending",
                    "intervention_request": {"request_id": "r2", "type": "clarification"},
                },
            ],
        }
        # r1 already emitted, r2 is new
        seen = {"r1"}
        results = _normalize_stream_event(
            chunk, "t1", "run-1", _emitted_intervention_ids=seen
        )
        intv = [r for r in results if r[0] == "intervention_requested"]
        assert len(intv) == 1
        assert intv[0][1]["request_id"] == "r2"
        # Verify r2 was added to the seen set (mutated in-place)
        assert "r2" in seen

    def test_normalize_values_artifact_count_dedup(self):
        """Artifacts with same count as last should not be re-emitted."""
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "artifacts": [{"name": "old-artifact"}],
        }
        results = _normalize_stream_event(
            chunk, "t1", "run-1", _last_artifacts_count=1
        )
        artifact_events = [r for r in results if r[0] == "artifact_created"]
        assert len(artifact_events) == 0

    def test_normalize_values_multiple_artifacts_emits_all_new(self):
        """When artifact count increases by more than 1, each new artifact is emitted."""
        from src.gateway.runtime_service import SSE_ARTIFACT_CREATED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "artifacts": [
                {"name": "artifact-1", "artifact_url": "/a1"},
                {"name": "artifact-2", "artifact_url": "/a2"},
                {"name": "artifact-3", "artifact_url": "/a3"},
            ],
        }
        results = _normalize_stream_event(chunk, "t1", "run-1", _last_artifacts_count=0)
        artifact_events = [r for r in results if r[0] == SSE_ARTIFACT_CREATED]
        assert len(artifact_events) == 3
        assert artifact_events[0][1]["artifact"]["name"] == "artifact-1"
        assert artifact_events[1][1]["artifact"]["name"] == "artifact-2"
        assert artifact_events[2][1]["artifact"]["name"] == "artifact-3"

    def test_normalize_values_multiple_artifacts_emits_only_new_ones(self):
        """When some artifacts are already emitted, only new ones are emitted."""
        from src.gateway.runtime_service import SSE_ARTIFACT_CREATED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "artifacts": [
                {"name": "old-1", "artifact_url": "/o1"},
                {"name": "old-2", "artifact_url": "/o2"},
                {"name": "new-3", "artifact_url": "/n3"},
            ],
        }
        # 2 artifacts already seen
        results = _normalize_stream_event(chunk, "t1", "run-1", _last_artifacts_count=2)
        artifact_events = [r for r in results if r[0] == SSE_ARTIFACT_CREATED]
        assert len(artifact_events) == 1
        assert artifact_events[0][1]["artifact"]["name"] == "new-3"

    def test_normalize_values_governance_only_pending(self):
        """Only pending governance entries emit events."""
        from src.gateway.runtime_service import SSE_GOVERNANCE_CREATED, _normalize_stream_event

        chunk = MagicMock()
        chunk.event = "values"
        chunk.data = {
            "governance_queue": [{"id": "g1", "status": "approved"}],
        }
        results = _normalize_stream_event(chunk, "t1", "run-1")
        gov_events = [r for r in results if r[0] == SSE_GOVERNANCE_CREATED]
        assert len(gov_events) == 0

    def test_chunk_as_tuple_format(self):
        """Some LangGraph SDK versions yield (event, data) tuples."""
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = ("values", {"messages": [{"type": "ai", "content": "hello"}]})
        results = _normalize_stream_event(chunk, "t1", "run-1")
        msg_events = [r for r in results if r[0] == "message_completed"]
        assert len(msg_events) == 1

    def test_chunk_missing_event_returns_empty(self):
        from src.gateway.runtime_service import _normalize_stream_event

        chunk = MagicMock()
        chunk.event = None
        chunk.data = {"foo": "bar"}
        results = _normalize_stream_event(chunk, "t1", "run-1")
        assert results == []


class TestRuntimeServiceErrorSanitization:
    """Test that internal error details are never leaked to external clients."""

    def test_sanitize_hides_internal_ip(self):
        from src.gateway.runtime_service import _sanitize_error

        result = _sanitize_error(ConnectionError("connection refused to 192.168.1.100:2024"))
        assert "192.168.1.100" not in result
        assert result == "Upstream runtime unavailable"

    def test_sanitize_timeout(self):
        from src.gateway.runtime_service import _sanitize_error

        result = _sanitize_error(TimeoutError("timed out after 30s"))
        assert result == "Upstream runtime unavailable"

    def test_sanitize_conflict_409(self):
        from src.gateway.runtime_service import _sanitize_error

        result = _sanitize_error(Exception("409 Conflict: multitask strategy reject"))
        assert result == "Runtime rejected the submission"

    def test_sanitize_unknown_error(self):
        from src.gateway.runtime_service import _sanitize_error

        result = _sanitize_error(RuntimeError("unexpected internal bug"))
        assert result == "Runtime execution failed"
        assert "internal bug" not in result


class TestRuntimeServiceStreamMessage:
    """Tests for start_stream / iter_events two-phase behavior."""

    @patch("src.gateway.runtime_service._get_client")
    def test_upstream_connection_error_raises_runtime_service_error(self, mock_get_client):
        """Upstream connection failures raise RuntimeServiceError (not in-band SSE)."""
        from src.gateway.runtime_service import RuntimeServiceError, start_stream

        class _FailingRuns:
            async def stream(self, *args, **kwargs):
                raise ConnectionError("connection refused")
                yield  # noqa: unreachable

        client = MagicMock()
        client.runs = _FailingRuns()
        mock_get_client.return_value = client

        async def _call():
            return await start_stream(
                thread_id="t1", message="hello", context={"thread_id": "t1"},
            )

        with pytest.raises(RuntimeServiceError) as exc_info:
            asyncio.run(_call())
        assert exc_info.value.status_code == 503

    @patch("src.gateway.runtime_service._get_client")
    def test_on_submit_success_not_called_on_error(self, mock_get_client):
        """on_submit_success should NOT be called if upstream fails before first chunk."""
        from src.gateway.runtime_service import RuntimeServiceError, stream_message

        class _FailingRuns:
            async def stream(self, *args, **kwargs):
                raise Exception("upstream broke")
                yield

        client = MagicMock()
        client.runs = _FailingRuns()
        mock_get_client.return_value = client

        callback_called = False
        def callback():
            nonlocal callback_called
            callback_called = True

        async def _collect():
            return [chunk async for chunk in stream_message(
                thread_id="t1", message="hello", context={},
                on_submit_success=callback,
            )]

        with pytest.raises(RuntimeServiceError):
            asyncio.run(_collect())
        assert not callback_called


# ═══════════════════════════════════════════════════════════════════════
# 4. Batch Sync — Edge Cases, Partial Failures
# ═══════════════════════════════════════════════════════════════════════


def _make_sync_app(tmp_path: Path, tenant_id: str = "default"):
    """Create a FastAPI app with agents router for sync testing."""
    from src.gateway.routers import agents as agents_mod

    app = FastAPI()
    app.include_router(agents_mod.router)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)

    original_resolve = agents_mod._resolve_agents_dir
    agents_mod._resolve_agents_dir = lambda tid: agents_dir

    @app.middleware("http")
    async def inject_identity(request, call_next):
        request.state.tenant_id = tenant_id
        request.state.user_id = "user-1"
        request.state.role = "admin"
        return await call_next(request)

    def cleanup():
        agents_mod._resolve_agents_dir = original_resolve

    return app, agents_dir, cleanup


class TestBatchSyncEdgeCases:
    """Edge cases and robustness for POST /api/agents/sync."""

    def test_sync_upsert_with_all_fields(self, tmp_path):
        """Sync should handle all AgentConfig fields correctly."""
        app, agents_dir, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post("/api/agents/sync", json={
                "agents": [{
                    "name": "full-agent",
                    "description": "Full test",
                    "model": "gpt-4",
                    "engine_type": "ReAct",
                    "domain": "analytics",
                    "tool_groups": ["python", "web"],
                    "hitl_keywords": ["delete", "deploy"],
                    "max_tool_calls": 50,
                    "available_skills": ["csv-analyze"],
                    "requested_orchestration_mode": "workflow",
                    "soul": "You are a data analyst.",
                }],
                "mode": "upsert",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "full-agent" in data["created"]
        finally:
            cleanup()

    def test_sync_replace_with_empty_list_deletes_all(self, tmp_path):
        """Replace mode with empty list should delete all existing agents."""
        app, agents_dir, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)
            # First create an agent
            client.post("/api/agents/sync", json={
                "agents": [{"name": "doomed-agent", "soul": "I will be deleted"}],
                "mode": "upsert",
            })
            assert (agents_dir / "doomed-agent").exists()

            # Now replace with empty list
            resp = client.post("/api/agents/sync", json={
                "agents": [],
                "mode": "replace",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "doomed-agent" in data["deleted"]
            assert not (agents_dir / "doomed-agent").exists()
        finally:
            cleanup()

    def test_sync_upsert_empty_list_is_noop(self, tmp_path):
        """Upsert mode with empty list should not touch existing agents."""
        app, agents_dir, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)
            # Create an agent
            client.post("/api/agents/sync", json={
                "agents": [{"name": "safe-agent", "soul": "I should survive"}],
                "mode": "upsert",
            })
            assert (agents_dir / "safe-agent").exists()

            # Upsert with empty list
            resp = client.post("/api/agents/sync", json={
                "agents": [],
                "mode": "upsert",
            })
            assert resp.status_code == 200
            assert (agents_dir / "safe-agent").exists()  # Still there
        finally:
            cleanup()

    def test_sync_name_with_special_chars_rejected(self, tmp_path):
        """Names with dots, spaces, or special chars should be rejected."""
        app, _, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)
            for bad_name in ["agent.name", "agent name", "agent@name", "agent/name"]:
                resp = client.post("/api/agents/sync", json={
                    "agents": [{"name": bad_name}],
                    "mode": "upsert",
                })
                assert resp.status_code == 422, f"Expected 422 for name '{bad_name}', got {resp.status_code}"
        finally:
            cleanup()

    def test_sync_case_insensitive_duplicate_detection(self, tmp_path):
        """Duplicate detection should be case-insensitive."""
        app, _, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post("/api/agents/sync", json={
                "agents": [
                    {"name": "Agent-A"},
                    {"name": "agent-a"},
                ],
                "mode": "upsert",
            })
            assert resp.status_code == 422
            assert "duplicate" in resp.json()["detail"].lower()
        finally:
            cleanup()

    def test_sync_idempotent_upsert(self, tmp_path):
        """Running the same upsert twice should produce updated on second run."""
        app, _, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)
            payload = {
                "agents": [{"name": "idempotent-agent", "description": "v1", "soul": "test"}],
                "mode": "upsert",
            }
            resp1 = client.post("/api/agents/sync", json=payload)
            assert "idempotent-agent" in resp1.json()["created"]

            resp2 = client.post("/api/agents/sync", json=payload)
            assert "idempotent-agent" in resp2.json()["updated"]
        finally:
            cleanup()

    def test_sync_replace_with_upsert_errors_does_not_delete_existing(self, tmp_path):
        """BUG REGRESSION: replace mode must NOT delete existing agents when upsert errors occur.

        Previously, if an incoming agent failed to sync (e.g. bad config), the
        code would still proceed to delete all agents not in incoming_names.
        This meant a partial failure could wipe out the entire agent inventory.

        Expected behavior: when any upsert error occurs, skip the deletion phase
        entirely to prevent data loss.
        """
        app, agents_dir, cleanup = _make_sync_app(tmp_path)
        try:
            client = TestClient(app)

            # Step 1: Create two healthy agents
            resp = client.post("/api/agents/sync", json={
                "agents": [
                    {"name": "keep-a", "description": "should survive", "soul": "A"},
                    {"name": "keep-b", "description": "should survive", "soul": "B"},
                ],
                "mode": "upsert",
            })
            assert resp.status_code == 200
            assert (agents_dir / "keep-a").exists()
            assert (agents_dir / "keep-b").exists()

            # Step 2: Replace with a set that includes one bad agent.
            # We simulate a write failure by making the agent dir read-only
            # so _sync_upsert_agent fails for "bad-agent".
            # Instead, we use a simpler approach: patch _sync_upsert_agent to
            # fail for a specific agent.
            from unittest.mock import patch as _patch
            from src.gateway.routers.agents import AgentSyncItemResult

            original_upsert = None

            def _patched_upsert(agents_dir, name, item, existing_names):
                if name == "bad-agent":
                    return AgentSyncItemResult(name=name, action="failed", error="simulated failure")
                return original_upsert(agents_dir, name, item, existing_names)

            import src.gateway.routers.agents as agents_mod
            original_upsert = agents_mod._sync_upsert_agent
            agents_mod._sync_upsert_agent = _patched_upsert
            try:
                resp = client.post("/api/agents/sync", json={
                    "agents": [
                        {"name": "bad-agent", "soul": "will fail"},
                        {"name": "new-good", "soul": "will succeed"},
                    ],
                    "mode": "replace",
                })
                assert resp.status_code == 200
                data = resp.json()

                # bad-agent should be in errors
                assert any(e["name"] == "bad-agent" for e in data["errors"])

                # CRITICAL: keep-a and keep-b must NOT be deleted
                assert (agents_dir / "keep-a").exists(), "keep-a was deleted despite upsert errors!"
                assert (agents_dir / "keep-b").exists(), "keep-b was deleted despite upsert errors!"
                assert data["deleted"] == [], "No agents should be deleted when upsert had errors"
            finally:
                agents_mod._sync_upsert_agent = original_upsert
        finally:
            cleanup()


# ═══════════════════════════════════════════════════════════════════════
# 5. allowed_agents Contract — Integration Verification
# ═══════════════════════════════════════════════════════════════════════


class TestAllowedAgentsContract:
    """Verify the allowed_agents contract from validation through planner/router."""

    def _write_domain_agent(self, agents_dir: Path, name: str, domain: str):
        """Write a minimal domain agent config."""
        agent_dir = agents_dir / name
        agent_dir.mkdir(parents=True, exist_ok=True)
        config = {"name": name, "description": f"Test {name}", "domain": domain}
        (agent_dir / "config.yaml").write_text(
            "\n".join(f"{k}: {v}" for k, v in config.items()),
            encoding="utf-8",
        )

    def test_list_domain_agents_empty_allowed_list_returns_nothing(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        agents_dir = tmp_path / "agents"
        self._write_domain_agent(agents_dir, "agent-a", "research")
        self._write_domain_agent(agents_dir, "agent-b", "analysis")

        result = list_domain_agents(agents_dir=agents_dir, allowed_agents=[])
        assert result == []

    def test_list_domain_agents_filters_non_domain_agents(self, tmp_path):
        """Agents without a domain field should never appear regardless of allowlist."""
        from src.config.agents_config import list_domain_agents

        agents_dir = tmp_path / "agents"
        # Agent without domain
        no_domain_dir = agents_dir / "no-domain"
        no_domain_dir.mkdir(parents=True)
        (no_domain_dir / "config.yaml").write_text("name: no-domain\ndescription: test", encoding="utf-8")

        self._write_domain_agent(agents_dir, "with-domain", "research")

        result = list_domain_agents(agents_dir=agents_dir, allowed_agents=["no-domain", "with-domain"])
        names = [a.name for a in result]
        assert "no-domain" not in names
        assert "with-domain" in names

    def test_list_domain_agents_case_insensitive_match(self, tmp_path):
        from src.config.agents_config import list_domain_agents

        agents_dir = tmp_path / "agents"
        self._write_domain_agent(agents_dir, "Research-Bot", "research")

        # Try with different cases in allowed_agents
        result = list_domain_agents(agents_dir=agents_dir, allowed_agents=["research-bot"])
        # This depends on how agent names are stored — directory name is "Research-Bot"
        # but config.yaml has "name: Research-Bot"
        # The filter does: a.name.lower() in allowed_set
        # So "research-bot" in {"research-bot"} should match
        assert len(result) == 1

    def test_validate_allowed_agents_normalizes_case(self, tmp_path):
        """_validate_allowed_agents should normalize to lowercase."""
        from src.gateway.routers import runtime as runtime_mod

        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "my-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: my-agent\ndescription: test", encoding="utf-8")

        original = runtime_mod._resolve_agents_dir
        runtime_mod._resolve_agents_dir = lambda tid: agents_dir
        try:
            result = runtime_mod._validate_allowed_agents(["My-Agent", "MY-AGENT"], "default")
            # Deduplicated to single lowercase entry
            assert result == ["my-agent"]
        finally:
            runtime_mod._resolve_agents_dir = original

    def test_validate_allowed_agents_rejects_nonexistent(self, tmp_path):
        """Unknown agent directories should cause 422."""
        from fastapi import HTTPException
        from src.gateway.routers import runtime as runtime_mod

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        original = runtime_mod._resolve_agents_dir
        runtime_mod._resolve_agents_dir = lambda tid: agents_dir
        try:
            with pytest.raises(HTTPException) as exc_info:
                runtime_mod._validate_allowed_agents(["ghost-agent"], "default")
            assert exc_info.value.status_code == 422
        finally:
            runtime_mod._resolve_agents_dir = original

    def test_validate_allowed_agents_rejects_bare_directory_without_config(self, tmp_path):
        """BUG REGRESSION: A directory without config.yaml should NOT be treated as a valid agent.

        Previously, _validate_allowed_agents only checked agent_dir.is_dir(),
        allowing an empty directory to pass validation. The planner/router would
        then see zero usable agents, silently breaking the execution.
        """
        from fastapi import HTTPException
        from src.gateway.routers import runtime as runtime_mod

        agents_dir = tmp_path / "agents"
        # Create a bare directory — no config.yaml
        ghost_dir = agents_dir / "ghost-agent"
        ghost_dir.mkdir(parents=True)

        original = runtime_mod._resolve_agents_dir
        runtime_mod._resolve_agents_dir = lambda tid: agents_dir
        try:
            with pytest.raises(HTTPException) as exc_info:
                runtime_mod._validate_allowed_agents(["ghost-agent"], "default")
            assert exc_info.value.status_code == 422
            assert "ghost-agent" in exc_info.value.detail.lower()
        finally:
            runtime_mod._resolve_agents_dir = original


class TestThreadCreateEndToEnd:
    """End-to-end thread lifecycle tests."""

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_create_then_get_thread(self, mock_state, mock_create, tmp_path):
        """Create a thread, then GET it — full lifecycle."""
        mock_create.return_value = {"thread_id": "thread-lifecycle"}
        mock_state.return_value = {
            "title": None, "run_id": None, "workflow_stage": None,
            "workflow_stage_detail": None, "artifacts_count": 0, "pending_intervention": False,
        }

        app, reg, cleanup = _make_runtime_app(tmp_path)
        try:
            client = TestClient(app)

            # Create
            resp = client.post("/api/runtime/threads", json={"portal_session_id": "sess-e2e"})
            assert resp.status_code == 201
            thread_id = resp.json()["thread_id"]
            assert thread_id == "thread-lifecycle"

            # Get
            resp = client.get(f"/api/runtime/threads/{thread_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["portal_session_id"] == "sess-e2e"
            assert data["tenant_id"] == "default"
            assert data["user_id"] == "user-1"
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_create_thread_then_stream_updates_binding(self, mock_create, tmp_path):
        """Create thread → submit message → verify binding updated."""
        mock_create.return_value = {"thread_id": "thread-full"}

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        my_agent_dir = agents_dir / "my-agent"
        my_agent_dir.mkdir()
        (my_agent_dir / "config.yaml").write_text("name: my-agent\ndescription: test", encoding="utf-8")

        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            client = TestClient(app)

            # Create
            resp = client.post("/api/runtime/threads", json={"portal_session_id": "sess-full"})
            assert resp.status_code == 201

            # Stream
            with patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock) as mock_start, \
                 patch("src.gateway.routers.runtime.iter_events") as mock_iter:
                mock_start.return_value = (None, None)
                async def fake_iter(**kw):
                    yield 'event: ack\ndata: {}\n\n'
                mock_iter.side_effect = lambda **kw: fake_iter(**kw)

                resp = client.post(
                    "/api/runtime/threads/thread-full/messages:stream",
                    json={
                        "message": "analyze this",
                        "group_key": "analytics-team",
                        "allowed_agents": ["my-agent"],
                        "entry_agent": "my-agent",
                        "requested_orchestration_mode": "workflow",
                    },
                )
                assert resp.status_code == 200

            # Verify binding
            binding = reg.get_binding("thread-full")
            assert binding["portal_session_id"] == "sess-full"
            assert binding["group_key"] == "analytics-team"
            assert binding["allowed_agents"] == ["my-agent"]
            assert binding["entry_agent"] == "my-agent"
            assert binding["requested_orchestration_mode"] == "workflow"
        finally:
            cleanup()


# ═══════════════════════════════════════════════════════════════════════
# 6. SSE Format Validation
# ═══════════════════════════════════════════════════════════════════════


class TestSSEFormatCompliance:
    """Verify SSE frames conform to the documented contract."""

    # ── Pydantic-level field limits ──

    def test_message_exceeding_max_length_rejected(self, tmp_path):
        """message > MAX_MESSAGE_LENGTH should be rejected by Pydantic."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "x" * 100_001, "group_key": "g", "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 422
        finally:
            cleanup()

    def test_group_key_exceeding_max_length_rejected(self, tmp_path):
        """group_key > MAX_GROUP_KEY_LENGTH should be rejected by Pydantic."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hi", "group_key": "g" * 129, "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 422
        finally:
            cleanup()

    def test_allowed_agents_exceeding_max_count_rejected(self, tmp_path):
        """allowed_agents > MAX_ALLOWED_AGENTS should be rejected by Pydantic."""
        agents_dir = self._setup_agents(tmp_path)
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        try:
            reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hi", "group_key": "g",
                    "allowed_agents": [f"agent-{i}" for i in range(101)],
                },
            )
            assert resp.status_code == 422
        finally:
            cleanup()


class TestMetadataPersistence:
    """Verify metadata is persisted in thread binding after successful submission."""

    def _setup(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for name in ("agent-a",):
            d = agents_dir / name
            d.mkdir()
            (d / "config.yaml").write_text(f"name: {name}\ndescription: test", encoding="utf-8")
        app, reg, cleanup = _make_runtime_app(tmp_path, agents_dir)
        reg.register_binding("thread-1", tenant_id="default", user_id="user-1", portal_session_id="s")
        return app, reg, cleanup

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_metadata_persisted_on_success(self, mock_start, mock_iter, tmp_path):
        mock_start.return_value = (None, None)
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        app, reg, cleanup = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hi", "group_key": "team",
                    "allowed_agents": ["agent-a"],
                    "metadata": {"source": "portal", "version": 2},
                },
            )
            assert resp.status_code == 200
            binding = reg.get_binding("thread-1")
            assert binding["metadata"] == {"source": "portal", "version": 2}
        finally:
            cleanup()

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_null_metadata_persisted_as_none(self, mock_start, mock_iter, tmp_path):
        mock_start.return_value = (None, None)
        async def fake_iter(**kw):
            yield 'event: ack\ndata: {}\n\n'
        mock_iter.side_effect = lambda **kw: fake_iter(**kw)

        app, reg, cleanup = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hi", "group_key": "team", "allowed_agents": ["agent-a"]},
            )
            assert resp.status_code == 200
            binding = reg.get_binding("thread-1")
            assert binding.get("metadata") is None


        finally:
            cleanup()


# ═══════════════════════════════════════════════════════════════════════
# 6. SSE Format Validation
# ═══════════════════════════════════════════════════════════════════════


class TestSSEFormatCompliance:
    """Verify SSE frames conform to the documented contract."""

    def test_format_sse_structure(self):
        from src.gateway.runtime_service import _format_sse
        frame = _format_sse("test_event", {"key": "value"})
        lines = frame.split("\n")
        assert lines[0] == "event: test_event"
        assert lines[1].startswith("data: ")
        assert lines[2] == ""
        assert lines[3] == ""
        parsed = json.loads(lines[1][6:])
        assert parsed == {"key": "value"}

    def test_format_sse_unicode_preserved(self):
        from src.gateway.runtime_service import _format_sse
        frame = _format_sse("msg", {"content": "中文内容"})
        assert "中文内容" in frame

    def test_all_event_names_match_contract(self):
        """Verify all SSE event name constants match the documented external contract."""
        from src.gateway.runtime_service import (
            SSE_ACK, SSE_MESSAGE_DELTA, SSE_MESSAGE_COMPLETED,
            SSE_ARTIFACT_CREATED, SSE_INTERVENTION_REQUESTED,
            SSE_GOVERNANCE_CREATED, SSE_RUN_COMPLETED, SSE_RUN_FAILED,
        )
        expected_events = {
            "ack", "message_delta", "message_completed",
            "artifact_created", "intervention_requested",
            "governance_created", "run_completed", "run_failed",
        }
        actual_events = {
            SSE_ACK, SSE_MESSAGE_DELTA, SSE_MESSAGE_COMPLETED,
            SSE_ARTIFACT_CREATED, SSE_INTERVENTION_REQUESTED,
            SSE_GOVERNANCE_CREATED, SSE_RUN_COMPLETED, SSE_RUN_FAILED,
        }
        assert actual_events == expected_events
