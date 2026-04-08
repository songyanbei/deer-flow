from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.lifecycle_manager import LifecycleManager
from src.config.paths import Paths
from src.gateway.thread_context import ThreadContext
from src.gateway.thread_registry import ThreadRegistry


def _make_identity_app(router, *, tenant_id: str | None, user_id: str | None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def inject_identity(request, call_next):
        if tenant_id is not None:
            request.state.tenant_id = tenant_id
        if user_id is not None:
            request.state.user_id = user_id
        request.state.username = "tester"
        request.state.role = "admin"
        return await call_next(request)

    app.include_router(router)
    return app


def _runtime_registry_patches(registry: ThreadRegistry):
    return (
        patch("src.gateway.routers.runtime.get_thread_registry", return_value=registry),
        patch("src.gateway.thread_registry.get_thread_registry", return_value=registry),
    )


class TestDirectoryIsolation:
    @pytest.mark.parametrize(
        ("tenant_id", "user_id", "thread_id"),
        [
            ("tenant-a", "user-1", "thread-a1"),
            ("tenant-a", "user-1", "thread-a2"),
            ("tenant-a", "user-2", "thread-a3"),
            ("tenant-b", "user-3", "thread-b1"),
        ],
    )
    def test_paths_land_under_tenant_user_thread_tree(self, tmp_path, tenant_id, user_id, thread_id):
        paths = Paths(base_dir=tmp_path)
        ctx = ThreadContext(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)

        paths.ensure_thread_dirs_ctx(ctx)

        thread_dir = paths.thread_dir_ctx(ctx)
        assert thread_dir == tmp_path / "tenants" / tenant_id / "users" / user_id / "threads" / thread_id
        assert paths.sandbox_work_dir_ctx(ctx).is_dir()
        assert paths.sandbox_uploads_dir_ctx(ctx).is_dir()
        assert paths.sandbox_outputs_dir_ctx(ctx).is_dir()
        assert str(paths.sandbox_user_data_dir_ctx(ctx)).startswith(str(thread_dir))

    def test_thread_context_path_resolution_rejects_symlink_escape(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        ctx = ThreadContext(tenant_id="tenant-a", user_id="user-1", thread_id="thread-a1")
        paths.ensure_thread_dirs_ctx(ctx)

        external_dir = tmp_path / "external"
        external_dir.mkdir(parents=True)
        link_path = paths.sandbox_outputs_dir_ctx(ctx) / "escape-link"

        try:
            link_path.symlink_to(external_dir, target_is_directory=True)
        except (NotImplementedError, OSError):
            pytest.skip("symlink creation is unavailable in this environment")

        with pytest.raises(ValueError, match="traversal"):
            paths.resolve_virtual_path_ctx(ctx, "/mnt/user-data/outputs/escape-link/secret.txt")

    def test_concurrent_identity_matrix_pressure_keeps_directories_isolated(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        matrix = [
            ("tenant-a", "user-1", "thread-a1"),
            ("tenant-a", "user-1", "thread-a2"),
            ("tenant-a", "user-2", "thread-a3"),
            ("tenant-b", "user-3", "thread-b1"),
        ]

        def worker(index: int) -> tuple[str, str]:
            tenant_id, user_id, thread_id = matrix[index % len(matrix)]
            registry.register(thread_id, tenant_id, user_id=user_id)
            ctx = ThreadContext(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
            paths.ensure_thread_dirs_ctx(ctx)

            marker = f"iter-{index}"
            (paths.sandbox_work_dir_ctx(ctx) / f"{marker}.txt").write_text(marker, encoding="utf-8")
            (paths.sandbox_uploads_dir_ctx(ctx) / f"{marker}.txt").write_text(marker, encoding="utf-8")
            output_path = paths.resolve_virtual_path_ctx(ctx, f"/mnt/user-data/outputs/{marker}.txt")
            output_path.write_text(marker, encoding="utf-8")
            return thread_id, marker

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(48)))

        per_thread_markers: dict[str, set[str]] = {}
        for thread_id, marker in results:
            per_thread_markers.setdefault(thread_id, set()).add(marker)

        for tenant_id, user_id, thread_id in matrix:
            ctx = ThreadContext(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
            discovered = {path.stem for path in paths.sandbox_user_data_dir_ctx(ctx).rglob("*.txt")}
            assert per_thread_markers[thread_id].issubset(discovered)


class TestIdentityPropagation:
    def test_thread_data_middleware_prefers_serialized_thread_context(self, tmp_path):
        from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware

        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        runtime = SimpleNamespace(
            context={"thread_id": "runtime-thread", "tenant_id": "runtime-tenant", "user_id": "runtime-user"},
        )

        with (
            patch("src.agents.middlewares.thread_data_middleware.get_config") as mock_config,
            patch("src.agents.middlewares.thread_data_middleware.get_thread_registry") as mock_registry,
        ):
            mock_config.return_value = {
                "configurable": {
                    "thread_context": {
                        "tenant_id": "tenant-a",
                        "user_id": "user-1",
                        "thread_id": "thread-a1",
                    }
                }
            }
            middleware.before_agent({}, runtime)

        mock_registry.return_value.register.assert_called_once_with("thread-a1", "tenant-a", user_id="user-1")

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_runtime_stream_injects_serialized_thread_context(self, mock_start, mock_iter, tmp_path):
        from src.gateway.routers import runtime

        captured_context: dict = {}
        agents_dir = tmp_path / "agents"
        planner_dir = agents_dir / "planner"
        planner_dir.mkdir(parents=True)
        (planner_dir / "config.yaml").write_text("name: planner\ndescription: test", encoding="utf-8")

        async def fake_start(*, thread_id, message, context):
            captured_context.update(context)
            return (None, None)

        async def fake_iter(**kwargs):
            yield "event: ack\\ndata: {}\\n\\n"

        mock_start.side_effect = fake_start
        mock_iter.side_effect = lambda **kwargs: fake_iter(**kwargs)

        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        registry.register_binding(
            "thread-a1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        app = _make_identity_app(runtime.router, tenant_id="tenant-a", user_id="user-1")

        patch_a, patch_b = _runtime_registry_patches(registry)
        with patch_a, patch_b, patch("src.gateway.routers.runtime._resolve_agents_dir", return_value=agents_dir):
            client = TestClient(app)
            response = client.post(
                "/api/runtime/threads/thread-a1/messages:stream",
                json={"message": "hello", "group_key": "g", "allowed_agents": ["planner"]},
            )

        assert response.status_code == 200
        assert captured_context["thread_context"] == {
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "thread_id": "thread-a1",
        }


class TestRouterIsolation:
    @patch("src.gateway.routers.runtime.get_thread_state_summary", new_callable=AsyncMock)
    def test_runtime_endpoint_rejects_cross_tenant_cross_user_and_unregistered(self, mock_state, tmp_path):
        from src.gateway.routers import runtime

        mock_state.return_value = {
            "title": None,
            "run_id": None,
            "workflow_stage": None,
            "workflow_stage_detail": None,
            "artifacts_count": 0,
            "pending_intervention": False,
        }

        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        registry.register_binding(
            "thread-a1",
            tenant_id="tenant-a",
            user_id="user-1",
            portal_session_id="sess-1",
        )

        patch_a, patch_b = _runtime_registry_patches(registry)
        with patch_a, patch_b:
            same_tenant_other_user = TestClient(
                _make_identity_app(runtime.router, tenant_id="tenant-a", user_id="user-2")
            )
            assert same_tenant_other_user.get("/api/runtime/threads/thread-a1").status_code == 403

            other_tenant_same_user = TestClient(
                _make_identity_app(runtime.router, tenant_id="tenant-b", user_id="user-1")
            )
            assert other_tenant_same_user.get("/api/runtime/threads/thread-a1").status_code == 403

            owner = TestClient(_make_identity_app(runtime.router, tenant_id="tenant-a", user_id="user-1"))
            assert owner.get("/api/runtime/threads/unknown-thread").status_code == 403

    def test_runtime_endpoint_returns_401_when_oidc_identity_missing(self):
        from src.gateway.routers import runtime

        app = FastAPI()
        app.include_router(runtime.router)
        client = TestClient(app)

        with patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            response = client.get("/api/runtime/threads/thread-a1")

        assert response.status_code == 401

    def test_artifact_endpoint_enforces_ownership_and_not_found_semantics(self, tmp_path):
        from src.gateway.routers import artifacts

        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        owner_ctx = ThreadContext(tenant_id="tenant-a", user_id="user-1", thread_id="thread-a1")
        registry.register(owner_ctx.thread_id, owner_ctx.tenant_id, user_id=owner_ctx.user_id)
        paths.ensure_thread_dirs_ctx(owner_ctx)
        (paths.sandbox_outputs_dir_ctx(owner_ctx) / "report.txt").write_text("owner-artifact", encoding="utf-8")

        with (
            patch("src.gateway.thread_registry.get_thread_registry", return_value=registry),
            patch("src.gateway.path_utils.get_paths", return_value=paths),
        ):
            owner_client = TestClient(
                _make_identity_app(artifacts.router, tenant_id="tenant-a", user_id="user-1")
            )
            assert owner_client.get(
                "/api/threads/thread-a1/artifacts/mnt/user-data/outputs/report.txt"
            ).status_code == 200
            assert owner_client.get(
                "/api/threads/thread-a1/artifacts/mnt/user-data/outputs/missing.txt"
            ).status_code == 404

            other_user_client = TestClient(
                _make_identity_app(artifacts.router, tenant_id="tenant-a", user_id="user-2")
            )
            assert other_user_client.get(
                "/api/threads/thread-a1/artifacts/mnt/user-data/outputs/report.txt"
            ).status_code == 403

            other_tenant_client = TestClient(
                _make_identity_app(artifacts.router, tenant_id="tenant-b", user_id="user-1")
            )
            assert other_tenant_client.get(
                "/api/threads/thread-a1/artifacts/mnt/user-data/outputs/report.txt"
            ).status_code == 403

    def test_uploads_endpoint_rejects_cross_user_access(self, tmp_path):
        from src.gateway.routers import uploads

        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        owner_ctx = ThreadContext(tenant_id="tenant-a", user_id="user-2", thread_id="thread-a3")
        registry.register(owner_ctx.thread_id, owner_ctx.tenant_id, user_id=owner_ctx.user_id)
        paths.ensure_thread_dirs_ctx(owner_ctx)
        (paths.sandbox_uploads_dir_ctx(owner_ctx) / "secret.txt").write_text("secret", encoding="utf-8")

        provider = MagicMock()
        provider.acquire.return_value = "local"
        provider.get.return_value = MagicMock()

        with (
            patch("src.gateway.thread_registry.get_thread_registry", return_value=registry),
            patch("src.gateway.routers.uploads.get_paths", return_value=paths),
            patch("src.gateway.routers.uploads.get_sandbox_provider", return_value=provider),
        ):
            owner_client = TestClient(
                _make_identity_app(uploads.router, tenant_id="tenant-a", user_id="user-2")
            )
            assert owner_client.get("/api/threads/thread-a3/uploads/list").status_code == 200

            other_user_client = TestClient(
                _make_identity_app(uploads.router, tenant_id="tenant-a", user_id="user-1")
            )
            assert other_user_client.get("/api/threads/thread-a3/uploads/list").status_code == 403


class TestLifecycleCleanup:
    def test_delete_user_cleans_target_user_data_and_sandbox_state_only(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        registry.register("thread-a1", "tenant-a", user_id="user-1")
        registry.register("thread-a2", "tenant-a", user_id="user-1")
        registry.register("thread-a3", "tenant-a", user_id="user-2")

        for ctx in [
            ThreadContext("tenant-a", "user-1", "thread-a1"),
            ThreadContext("tenant-a", "user-1", "thread-a2"),
            ThreadContext("tenant-a", "user-2", "thread-a3"),
        ]:
            paths.ensure_thread_dirs_ctx(ctx)
            paths.sandbox_state_dir(ctx.thread_id).mkdir(parents=True, exist_ok=True)
            (paths.sandbox_state_dir(ctx.thread_id) / "sandbox.json").write_text("{}", encoding="utf-8")

        manager = LifecycleManager(
            registry=registry,
            queue=MagicMock(cancel_by_user=MagicMock(return_value=0)),
            ledger=MagicMock(archive_by_user=MagicMock(return_value=0)),
        )

        with patch("src.config.paths.get_paths", return_value=paths):
            result = manager.delete_user("tenant-a", "user-1")

        assert result.threads_removed == 2
        assert not paths.tenant_user_dir("tenant-a", "user-1").exists()
        assert not paths.sandbox_state_dir("thread-a1").exists()
        assert not paths.sandbox_state_dir("thread-a2").exists()
        assert paths.tenant_user_dir("tenant-a", "user-2").exists()
        assert paths.sandbox_state_dir("thread-a3").exists()

    def test_decommission_tenant_preserves_other_tenant_data(self, tmp_path):
        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        registry.register("thread-a1", "tenant-a", user_id="user-1")
        registry.register("thread-b1", "tenant-b", user_id="user-3")

        for ctx in [
            ThreadContext("tenant-a", "user-1", "thread-a1"),
            ThreadContext("tenant-b", "user-3", "thread-b1"),
        ]:
            paths.ensure_thread_dirs_ctx(ctx)
            paths.sandbox_state_dir(ctx.thread_id).mkdir(parents=True, exist_ok=True)
            (paths.sandbox_state_dir(ctx.thread_id) / "sandbox.json").write_text("{}", encoding="utf-8")

        manager = LifecycleManager(
            registry=registry,
            queue=MagicMock(cancel_by_tenant=MagicMock(return_value=0)),
            ledger=MagicMock(purge_by_tenant=MagicMock(return_value=0)),
        )

        with patch("src.config.paths.get_paths", return_value=paths):
            result = manager.decommission_tenant("tenant-a")

        assert result.threads_removed == 1
        assert not paths.tenant_dir("tenant-a").exists()
        assert not paths.sandbox_state_dir("thread-a1").exists()
        assert paths.tenant_dir("tenant-b").exists()
        assert paths.sandbox_state_dir("thread-b1").exists()

    def test_cleanup_expired_threads_removes_new_legacy_and_sandbox_state_paths(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        paths = Paths(base_dir=tmp_path)
        registry = ThreadRegistry(registry_file=tmp_path / "thread_registry.json")
        registry.register("thread-a1", "tenant-a", user_id="user-1")
        registry.register("thread-b1", "tenant-b", user_id="user-3")

        old_created_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        data = registry._load()
        data["thread-a1"]["created_at"] = old_created_at
        registry._save(data)

        expired_ctx = ThreadContext("tenant-a", "user-1", "thread-a1")
        survivor_ctx = ThreadContext("tenant-b", "user-3", "thread-b1")
        paths.ensure_thread_dirs_ctx(expired_ctx)
        paths.ensure_thread_dirs_ctx(survivor_ctx)
        paths.thread_dir("thread-a1").mkdir(parents=True, exist_ok=True)
        paths.sandbox_state_dir("thread-a1").mkdir(parents=True, exist_ok=True)
        paths.sandbox_state_dir("thread-b1").mkdir(parents=True, exist_ok=True)

        manager = LifecycleManager(registry=registry, queue=MagicMock(), ledger=MagicMock())

        with patch("src.config.paths.get_paths", return_value=paths):
            result = manager.cleanup_expired_threads(max_age_seconds=86400 * 7)

        assert result.threads_removed == 1
        assert not paths.thread_dir_ctx(expired_ctx).exists()
        assert not paths.thread_dir("thread-a1").exists()
        assert not paths.sandbox_state_dir("thread-a1").exists()
        assert paths.thread_dir_ctx(survivor_ctx).exists()
        assert paths.sandbox_state_dir("thread-b1").exists()
