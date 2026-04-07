"""Regression tests for Admin router — cross-tenant rejection, tenant-scoped
expired-threads, RBAC role rejection, and lifecycle failure compensation."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.admin.lifecycle_manager import LifecycleResult


@pytest.fixture
def app():
    from src.gateway.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def _mock_result(**overrides) -> LifecycleResult:
    r = LifecycleResult()
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def _set_request_state(request, tenant_id, user_id="u1", role="admin"):
    """Helper to inject identity into request.state for OIDC-enabled tests."""
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    request.state.role = role


# ── Cross-tenant rejection (OIDC enabled) ────────────────────────────


class TestCrossTenantRejection:
    """Admin endpoints must reject cross-tenant operations when OIDC is on."""

    def test_delete_user_cross_tenant_rejected(self, app, client):
        """DELETE /users/{uid}?tenant_id=OTHER must return 403 when caller's
        tenant differs from the target tenant_id."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="tenant-caller")
            return await call_next(request)

        with patch("src.admin.router._get_manager"), \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            resp = client.delete("/api/admin/users/u1?tenant_id=tenant-other")
        assert resp.status_code == 403
        assert "another tenant" in resp.json()["detail"].lower()

    def test_decommission_cross_tenant_rejected(self, app, client):
        """DELETE /tenants/{tid} must return 403 when caller != target."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="tenant-caller")
            return await call_next(request)

        with patch("src.admin.router._get_manager"), \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            resp = client.delete("/api/admin/tenants/tenant-other")
        assert resp.status_code == 403

    def test_delete_user_same_tenant_allowed(self, app, client):
        """DELETE /users/{uid}?tenant_id=SAME should succeed (200)."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="tenant-a")
            return await call_next(request)

        with patch("src.admin.router._get_manager") as mock_mgr, \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            mock_mgr.return_value.delete_user.return_value = _mock_result(threads_removed=1)
            resp = client.delete("/api/admin/users/u1?tenant_id=tenant-a")
        assert resp.status_code == 200


# ── Expired-threads tenant scoping ────────────────────────────────────


class TestExpiredThreadsTenantScope:
    """POST /cleanup/expired-threads should pass the caller's tenant_id
    to the lifecycle manager so only the caller's threads are cleaned."""

    def test_passes_caller_tenant(self, app, client):
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="tenant-x")
            return await call_next(request)

        with patch("src.admin.router._get_manager") as mock_mgr, \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            mock_mgr.return_value.cleanup_expired_threads.return_value = _mock_result(threads_removed=2)
            resp = client.post("/api/admin/cleanup/expired-threads?max_age_seconds=3600")

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "tenant-x"
        # Verify tenant_id was actually passed to the manager
        mock_mgr.return_value.cleanup_expired_threads.assert_called_once_with(
            3600, tenant_id="tenant-x",
        )


# ── RBAC role rejection ──────────────────────────────────────────────


class TestRBACRoleRejection:
    """Admin endpoints should reject requests from non-admin/owner roles."""

    def test_member_role_rejected(self, app, client):
        """A user with role=member should get 403."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="t1", role="member")
            return await call_next(request)

        with patch("src.admin.router._get_manager"), \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            resp = client.delete("/api/admin/users/u1?tenant_id=t1")
        assert resp.status_code == 403
        assert "permission" in resp.json()["detail"].lower()

    def test_admin_role_allowed(self, app, client):
        """A user with role=admin should be allowed."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="t1", role="admin")
            return await call_next(request)

        with patch("src.admin.router._get_manager") as mock_mgr, \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            mock_mgr.return_value.delete_user.return_value = _mock_result()
            resp = client.delete("/api/admin/users/u1?tenant_id=t1")
        assert resp.status_code == 200

    def test_owner_role_allowed(self, app, client):
        """A user with role=owner should be allowed."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="t1", role="owner")
            return await call_next(request)

        with patch("src.admin.router._get_manager") as mock_mgr, \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            mock_mgr.return_value.decommission_tenant.return_value = _mock_result()
            resp = client.delete("/api/admin/tenants/t1")
        assert resp.status_code == 200


# ── Lifecycle failure compensation ────────────────────────────────────


class TestLifecycleFailureCompensation:
    """LifecycleManager should continue remaining steps when one fails,
    recording errors in the result."""

    def test_delete_user_continues_after_registry_failure(self):
        registry = MagicMock()
        registry.delete_threads_by_user.side_effect = RuntimeError("db lock")
        queue = MagicMock()
        queue.cancel_by_user.return_value = 2
        ledger = MagicMock()
        ledger.archive_by_user.return_value = 1

        from src.admin.lifecycle_manager import LifecycleManager
        mgr = LifecycleManager(registry=registry, queue=queue, ledger=ledger)
        result = mgr.delete_user("t1", "u1")

        assert result.has_errors
        assert any("delete_threads_by_user" in e for e in result.errors)
        # Remaining steps still executed
        assert result.memory_queue_cancelled == 2
        assert result.ledger_entries_removed == 1

    def test_decommission_continues_after_ledger_failure(self):
        registry = MagicMock()
        registry.delete_threads_by_tenant.return_value = 3
        queue = MagicMock()
        queue.cancel_by_tenant.return_value = 1
        ledger = MagicMock()
        ledger.purge_by_tenant.side_effect = OSError("disk full")

        from src.admin.lifecycle_manager import LifecycleManager
        mgr = LifecycleManager(registry=registry, queue=queue, ledger=ledger)
        result = mgr.decommission_tenant("t1")

        assert result.has_errors
        assert any("purge_by_tenant" in e for e in result.errors)
        assert result.threads_removed == 3
        assert result.memory_queue_cancelled == 1

    def test_no_errors_when_all_steps_succeed(self):
        registry = MagicMock()
        registry.delete_threads_by_user.return_value = 1
        queue = MagicMock()
        queue.cancel_by_user.return_value = 0
        ledger = MagicMock()
        ledger.archive_by_user.return_value = 0

        from src.admin.lifecycle_manager import LifecycleManager
        mgr = LifecycleManager(registry=registry, queue=queue, ledger=ledger)
        result = mgr.delete_user("t1", "u1")

        assert not result.has_errors
        assert result.errors is None

    def test_api_returns_partial_status_on_errors(self, app, client):
        """Admin API should return status=partial when lifecycle has errors."""
        @app.middleware("http")
        async def inject_oidc(request, call_next):
            _set_request_state(request, tenant_id="t1")
            return await call_next(request)

        error_result = _mock_result(threads_removed=0)
        error_result.add_error("delete_threads_by_user", RuntimeError("oops"))

        with patch("src.admin.router._get_manager") as mock_mgr, \
             patch.dict("os.environ", {"OIDC_ENABLED": "false"}):
            mock_mgr.return_value.delete_user.return_value = error_result
            resp = client.delete("/api/admin/users/u1?tenant_id=t1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "partial"
        assert "errors" in body
        assert len(body["errors"]) == 1


# ── Memory old-path write prohibition ─────────────────────────────────


class TestMemoryOldPathWriteProhibition:
    """When OIDC is enabled, _save_memory_to_file should refuse to write
    to tenant-level paths when user_id is missing."""

    def test_refuses_tenant_level_write_under_oidc(self, tmp_path):
        """Writing to tenant-level path (without user_id) must return False."""
        from src.agents.memory.updater import _save_memory_to_file

        with patch("src.agents.memory.updater.get_paths") as mock_paths, \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            mock_paths.return_value.tenant_memory_file.return_value = tmp_path / "t" / "memory.json"
            result = _save_memory_to_file(
                {"test": True}, agent_name=None,
                tenant_id="acme", user_id=None,
            )
        assert result is False

    def test_allows_user_level_write_under_oidc(self, tmp_path):
        """Writing to user-level path (with user_id) must succeed."""
        from src.agents.memory.updater import _save_memory_to_file

        target = tmp_path / "tenants" / "acme" / "users" / "alice" / "memory.json"
        with patch("src.agents.memory.updater.get_paths") as mock_paths, \
             patch.dict("os.environ", {"OIDC_ENABLED": "true"}):
            mock_paths.return_value.tenant_user_memory_file.return_value = target
            result = _save_memory_to_file(
                {"version": "1.0", "user": {}, "facts": []},
                agent_name=None, tenant_id="acme", user_id="alice",
            )
        assert result is True
        assert target.exists()

    def test_allows_tenant_level_write_without_oidc(self, tmp_path):
        """Without OIDC, tenant-level writes should be allowed (dev mode)."""
        from src.agents.memory.updater import _save_memory_to_file

        target = tmp_path / "tenants" / "acme" / "memory.json"
        with patch("src.agents.memory.updater.get_paths") as mock_paths, \
             patch.dict("os.environ", {"OIDC_ENABLED": "false"}):
            mock_paths.return_value.tenant_memory_file.return_value = target
            result = _save_memory_to_file(
                {"version": "1.0", "user": {}, "facts": []},
                agent_name=None, tenant_id="acme", user_id=None,
            )
        assert result is True
