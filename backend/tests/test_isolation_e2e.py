"""End-to-end integration tests for tenant/user isolation.

Verifies the complete flow:
  HTTP request (OIDC identity)
    → ThreadContext serialization into configurable
      → ThreadDataMiddleware creates tenant/user/thread dirs
        → SandboxMiddleware passes ThreadContext to provider

These tests complement the unit-level tests in test_multi_tenant.py,
test_thread_context.py, and test_tenant_user_thread_isolation_regression.py
by exercising the layers together.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.paths import Paths
from src.gateway.thread_context import ThreadContext
from src.gateway.thread_registry import ThreadRegistry


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_app(router, *, tenant_id: str, user_id: str) -> FastAPI:
    """Build a minimal FastAPI app with identity injection (simulating OIDC)."""
    app = FastAPI()

    @app.middleware("http")
    async def inject_identity(request, call_next):
        request.state.tenant_id = tenant_id
        request.state.user_id = user_id
        request.state.username = "tester"
        request.state.role = "admin"
        return await call_next(request)

    app.include_router(router)
    return app


def _registry_patches(registry: ThreadRegistry):
    """Return context managers that replace the global thread registry."""
    return (
        patch("src.gateway.routers.runtime.get_thread_registry", return_value=registry),
        patch("src.gateway.thread_registry.get_thread_registry", return_value=registry),
    )


def _validation_bypass():
    """Bypass agent validation (no real agents dir in test env)."""
    return (
        patch("src.gateway.routers.runtime._validate_allowed_agents", side_effect=lambda a, t, **kw: [x.strip().lower() for x in a]),
        patch("src.gateway.routers.runtime._validate_entry_agent", side_effect=lambda e, a: e),
    )


# ── Test class ───────────────────────────────────────────────────────────


class TestRequestToSandboxMountE2E:
    """End-to-end integration: OIDC identity → ThreadContext → middleware → sandbox."""

    # ── 1. Full HTTP flow ────────────────────────────────────────────────

    def test_identity_flows_from_request_through_middleware_to_sandbox(self, tmp_path: Path):
        """POST /messages:stream carries OIDC identity all the way to start_stream context."""
        from src.gateway.routers import runtime as runtime_mod

        registry = ThreadRegistry(registry_file=tmp_path / "registry.db")
        registry.register_binding(
            "thread-e2e",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )

        app = _make_app(runtime_mod.router, tenant_id="tenant-a", user_id="user-1")
        p1, p2 = _registry_patches(registry)

        captured_context: dict = {}

        async def fake_start(*, thread_id, message, context):
            captured_context.update(context)
            return ("chunk", iter([]))

        v1, v2 = _validation_bypass()
        with p1, p2, v1, v2, \
             patch.object(runtime_mod, "start_stream", new_callable=AsyncMock, side_effect=fake_start), \
             patch.object(runtime_mod, "iter_events", return_value=iter([])):

            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-e2e/messages:stream",
                json={"message": "hello", "group_key": "g", "allowed_agents": ["planner"]},
            )

        assert resp.status_code == 200

        # Identity fields present in runtime context
        assert captured_context["tenant_id"] == "tenant-a"
        assert captured_context["user_id"] == "user-1"
        assert captured_context["thread_id"] == "thread-e2e"

        # Serialized ThreadContext for middleware consumption
        tc = captured_context["thread_context"]
        assert tc == {"tenant_id": "tenant-a", "user_id": "user-1", "thread_id": "thread-e2e"}

    # ── 2. ThreadDataMiddleware creates dirs ──────────────────────────────

    def test_thread_data_middleware_creates_dirs_from_serialized_context(self, tmp_path: Path):
        """ThreadDataMiddleware reads ThreadContext from configurable and creates dirs."""
        from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware

        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "registry.db")

        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=False)

        fake_config = {
            "configurable": {
                "thread_context": {
                    "tenant_id": "tenant-a",
                    "user_id": "user-1",
                    "thread_id": "thread-e2e",
                },
            },
        }
        runtime = SimpleNamespace(context={"thread_id": "thread-e2e"})

        with patch("src.agents.middlewares.thread_data_middleware.get_config", return_value=fake_config), \
             patch("src.agents.middlewares.thread_data_middleware.get_thread_registry", return_value=registry):
            result = middleware.before_agent({}, runtime)

        # Directories created under tenant/user/thread hierarchy
        ctx = ThreadContext("tenant-a", "user-1", "thread-e2e")
        assert paths.sandbox_work_dir_ctx(ctx).is_dir()
        assert paths.sandbox_uploads_dir_ctx(ctx).is_dir()
        assert paths.sandbox_outputs_dir_ctx(ctx).is_dir()

        # Paths returned to agent state
        assert result["thread_data"]["workspace_path"] == str(paths.sandbox_work_dir_ctx(ctx))
        assert result["thread_data"]["uploads_path"] == str(paths.sandbox_uploads_dir_ctx(ctx))
        assert result["thread_data"]["outputs_path"] == str(paths.sandbox_outputs_dir_ctx(ctx))

        # Thread registered in registry with correct identity
        assert registry.get_tenant("thread-e2e") == "tenant-a"
        binding = registry.get_binding("thread-e2e")
        assert binding["user_id"] == "user-1"

    # ── 3. SandboxMiddleware passes context to provider ───────────────────

    def test_sandbox_middleware_passes_context_to_provider_before_acquire(self, tmp_path: Path):
        """SandboxMiddleware deserializes ThreadContext and calls provider in correct order."""
        from src.sandbox.middleware import SandboxMiddleware

        middleware = SandboxMiddleware(lazy_init=False)

        mock_provider = MagicMock()
        mock_provider.acquire.return_value = "sandbox-e2e"

        # Track call order
        call_log: list[str] = []
        mock_provider.set_thread_context.side_effect = lambda *a, **kw: call_log.append("set_ctx")
        mock_provider.acquire.side_effect = lambda *a, **kw: (call_log.append("acquire"), "sandbox-e2e")[1]

        fake_config = {
            "configurable": {
                "thread_context": {
                    "tenant_id": "tenant-a",
                    "user_id": "user-1",
                    "thread_id": "thread-e2e",
                },
            },
        }
        runtime = SimpleNamespace(context={"thread_id": "thread-e2e"})

        with patch("langgraph.config.get_config", return_value=fake_config), \
             patch("src.sandbox.middleware.get_sandbox_provider", return_value=mock_provider):
            result = middleware.before_agent({"sandbox": None}, runtime)

        # set_thread_context called with deserialized ThreadContext
        mock_provider.set_thread_context.assert_called_once()
        args = mock_provider.set_thread_context.call_args[0]
        assert args[0] == "thread-e2e"
        ctx_arg = args[1]
        assert isinstance(ctx_arg, ThreadContext)
        assert ctx_arg.tenant_id == "tenant-a"
        assert ctx_arg.user_id == "user-1"
        assert ctx_arg.thread_id == "thread-e2e"

        # acquire called AFTER set_thread_context
        assert call_log == ["set_ctx", "acquire"]

        # Sandbox ID returned in state
        assert result == {"sandbox": {"sandbox_id": "sandbox-e2e"}}

    # ── 3b. SandboxMiddleware rejects missing context under OIDC ────────

    def test_sandbox_middleware_rejects_missing_context_when_oidc_enabled(self, tmp_path: Path):
        """SandboxMiddleware raises RuntimeError if thread_context is missing and OIDC is on."""
        from src.sandbox.middleware import SandboxMiddleware

        middleware = SandboxMiddleware(lazy_init=False)

        mock_provider = MagicMock()
        mock_provider.acquire.return_value = "sandbox-e2e"

        # Config WITHOUT thread_context
        fake_config = {"configurable": {}}
        runtime = SimpleNamespace(context={"thread_id": "thread-e2e"})

        with patch("langgraph.config.get_config", return_value=fake_config), \
             patch("src.sandbox.middleware.get_sandbox_provider", return_value=mock_provider), \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            with pytest.raises(RuntimeError, match="thread_context.*required when OIDC is enabled"):
                middleware.before_agent({"sandbox": None}, runtime)

        # Provider should NOT have been called
        mock_provider.acquire.assert_not_called()

    def test_sandbox_middleware_allows_missing_context_when_oidc_disabled(self, tmp_path: Path):
        """Without OIDC, missing thread_context degrades gracefully (dev mode)."""
        from src.sandbox.middleware import SandboxMiddleware

        middleware = SandboxMiddleware(lazy_init=False)

        mock_provider = MagicMock()
        mock_provider.acquire.return_value = "sandbox-dev"

        fake_config = {"configurable": {}}
        runtime = SimpleNamespace(context={"thread_id": "thread-dev"})

        with patch("langgraph.config.get_config", return_value=fake_config), \
             patch("src.sandbox.middleware.get_sandbox_provider", return_value=mock_provider), \
             patch.dict("os.environ", {"OIDC_ENABLED": "false"}):
            result = middleware.before_agent({"sandbox": None}, runtime)

        # Graceful degradation — acquire still called
        mock_provider.acquire.assert_called_once_with("thread-dev")
        assert result == {"sandbox": {"sandbox_id": "sandbox-dev"}}

    # ── 4. Cross-tenant access rejected at gateway ────────────────────────

    def test_cross_tenant_request_rejected_at_gateway(self, tmp_path: Path):
        """Gateway rejects requests where caller identity doesn't match thread owner."""
        from src.gateway.routers import runtime as runtime_mod

        registry = ThreadRegistry(registry_file=tmp_path / "registry.db")
        registry.register_binding(
            "thread-owned",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        p1, p2 = _registry_patches(registry)

        # Wrong tenant
        app_wrong_tenant = _make_app(runtime_mod.router, tenant_id="tenant-b", user_id="user-1")
        with p1, p2:
            resp = TestClient(app_wrong_tenant).post(
                "/api/runtime/threads/thread-owned/messages:stream",
                json={"message": "hi", "group_key": "g", "allowed_agents": ["planner"]},
            )
        assert resp.status_code == 403

        # Wrong user
        app_wrong_user = _make_app(runtime_mod.router, tenant_id="tenant-a", user_id="user-2")
        p1, p2 = _registry_patches(registry)
        with p1, p2:
            resp = TestClient(app_wrong_user).post(
                "/api/runtime/threads/thread-owned/messages:stream",
                json={"message": "hi", "group_key": "g", "allowed_agents": ["planner"]},
            )
        assert resp.status_code == 403

        # Correct identity succeeds (mock start_stream to avoid real LangGraph call)
        app_ok = _make_app(runtime_mod.router, tenant_id="tenant-a", user_id="user-1")
        p1, p2 = _registry_patches(registry)

        async def fake_start(*, thread_id, message, context):
            return ("chunk", iter([]))

        v1, v2 = _validation_bypass()
        with p1, p2, v1, v2, \
             patch.object(runtime_mod, "start_stream", new_callable=AsyncMock, side_effect=fake_start), \
             patch.object(runtime_mod, "iter_events", return_value=iter([])):
            resp = TestClient(app_ok).post(
                "/api/runtime/threads/thread-owned/messages:stream",
                json={"message": "hi", "group_key": "g", "allowed_agents": ["planner"]},
            )
        assert resp.status_code == 200

    # ── 5. Path resolution matches ThreadContext ──────────────────────────

    def test_sandbox_path_resolution_matches_thread_context(self, tmp_path: Path):
        """Virtual sandbox paths resolve to the correct tenant/user/thread host dirs."""
        paths = Paths(base_dir=tmp_path)
        ctx = ThreadContext("tenant-a", "user-1", "thread-e2e")

        paths.ensure_thread_dirs_ctx(ctx)

        # Virtual → host resolution
        resolved = paths.resolve_virtual_path_ctx(ctx, "/mnt/user-data/outputs/report.pdf")
        expected = paths.sandbox_outputs_dir_ctx(ctx) / "report.pdf"
        assert resolved == expected

        # Workspace
        resolved_ws = paths.resolve_virtual_path_ctx(ctx, "/mnt/user-data/workspace/code.py")
        expected_ws = paths.sandbox_work_dir_ctx(ctx) / "code.py"
        assert resolved_ws == expected_ws

        # Traversal attack rejected
        with pytest.raises(ValueError, match="traversal"):
            paths.resolve_virtual_path_ctx(ctx, "/mnt/user-data/../../../../etc/passwd")

        # Wrong prefix rejected
        with pytest.raises(ValueError, match="must start with"):
            paths.resolve_virtual_path_ctx(ctx, "/etc/passwd")

        # Cross-tenant paths are structurally isolated
        ctx_b = ThreadContext("tenant-b", "user-2", "thread-other")
        paths.ensure_thread_dirs_ctx(ctx_b)
        resolved_a = paths.resolve_virtual_path_ctx(ctx, "/mnt/user-data/outputs/file.txt")
        resolved_b = paths.resolve_virtual_path_ctx(ctx_b, "/mnt/user-data/outputs/file.txt")
        assert resolved_a != resolved_b
        assert "tenant-a" in str(resolved_a)
        assert "tenant-b" in str(resolved_b)
