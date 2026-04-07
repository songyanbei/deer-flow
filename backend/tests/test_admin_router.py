"""Tests for Admin API router endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.admin.lifecycle_manager import LifecycleResult


@pytest.fixture
def client():
    """Create a test client with admin role mocked."""
    from src.gateway.app import create_app

    app = create_app()
    return TestClient(app)


def _mock_result(**overrides) -> LifecycleResult:
    r = LifecycleResult()
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


class TestDeleteUser:
    @patch("src.admin.router._get_manager")
    @patch("os.environ", {"OIDC_ENABLED": "false"})
    def test_delete_user(self, mock_mgr, client):
        mock_mgr.return_value.delete_user.return_value = _mock_result(
            threads_removed=3, memory_queue_cancelled=1,
            ledger_entries_removed=2, filesystem_cleaned=True,
        )
        resp = client.delete("/api/admin/users/u1?tenant_id=t1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["threads_removed"] == 3
        assert body["user_id"] == "u1"
        assert body["tenant_id"] == "t1"


class TestDecommissionTenant:
    @patch("src.admin.router._get_manager")
    @patch("os.environ", {"OIDC_ENABLED": "false"})
    def test_decommission_tenant(self, mock_mgr, client):
        mock_mgr.return_value.decommission_tenant.return_value = _mock_result(
            threads_removed=5, mcp_scopes_unloaded=True,
        )
        resp = client.delete("/api/admin/tenants/t1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["threads_removed"] == 5
        assert body["tenant_id"] == "t1"


class TestCleanupExpiredThreads:
    @patch("src.admin.router._get_manager")
    @patch("os.environ", {"OIDC_ENABLED": "false"})
    def test_cleanup(self, mock_mgr, client):
        mock_mgr.return_value.cleanup_expired_threads.return_value = _mock_result(
            threads_removed=10,
        )
        resp = client.post("/api/admin/cleanup/expired-threads?max_age_seconds=3600")
        assert resp.status_code == 200
        body = resp.json()
        assert body["threads_removed"] == 10
        assert body["max_age_seconds"] == 3600
