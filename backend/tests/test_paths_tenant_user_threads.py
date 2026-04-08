"""Tests for tenant/user/thread path model and sandbox_state_dir."""

from pathlib import Path

import pytest

from src.config.paths import Paths


@pytest.fixture
def paths(tmp_path):
    return Paths(base_dir=tmp_path)


class TestTenantUserThreadDir:
    def test_basic_path(self, paths, tmp_path):
        p = paths.tenant_user_thread_dir("acme", "alice", "th-1")
        assert p == tmp_path / "tenants" / "acme" / "users" / "alice" / "threads" / "th-1"

    def test_invalid_thread_id(self, paths):
        with pytest.raises(ValueError, match="Invalid thread_id"):
            paths.tenant_user_thread_dir("acme", "alice", "../escape")

    def test_invalid_tenant_id(self, paths):
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            paths.tenant_user_thread_dir("bad/tenant", "alice", "th-1")

    def test_invalid_user_id(self, paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.tenant_user_thread_dir("acme", "bad..user", "th-1")


class TestTenantUserSandboxDirs:
    def test_user_data_dir(self, paths, tmp_path):
        p = paths.tenant_user_sandbox_user_data_dir("t", "u", "th")
        expected = tmp_path / "tenants" / "t" / "users" / "u" / "threads" / "th" / "user-data"
        assert p == expected

    def test_work_dir(self, paths, tmp_path):
        p = paths.tenant_user_sandbox_work_dir("t", "u", "th")
        assert p.name == "workspace"
        assert "user-data" in str(p)

    def test_uploads_dir(self, paths, tmp_path):
        p = paths.tenant_user_sandbox_uploads_dir("t", "u", "th")
        assert p.name == "uploads"

    def test_outputs_dir(self, paths, tmp_path):
        p = paths.tenant_user_sandbox_outputs_dir("t", "u", "th")
        assert p.name == "outputs"


class TestEnsureTenantUserThreadDirs:
    def test_creates_all_dirs(self, paths):
        paths.ensure_tenant_user_thread_dirs("t", "u", "th")
        assert paths.tenant_user_sandbox_work_dir("t", "u", "th").is_dir()
        assert paths.tenant_user_sandbox_uploads_dir("t", "u", "th").is_dir()
        assert paths.tenant_user_sandbox_outputs_dir("t", "u", "th").is_dir()


class TestResolveTenantUserVirtualPath:
    def test_valid_workspace_path(self, paths):
        paths.ensure_tenant_user_thread_dirs("t", "u", "th")
        p = paths.resolve_tenant_user_virtual_path("t", "u", "th", "/mnt/user-data/workspace/script.py")
        expected = paths.tenant_user_sandbox_user_data_dir("t", "u", "th") / "workspace" / "script.py"
        assert p == expected.resolve()

    def test_bare_prefix(self, paths):
        paths.ensure_tenant_user_thread_dirs("t", "u", "th")
        p = paths.resolve_tenant_user_virtual_path("t", "u", "th", "/mnt/user-data")
        assert p == paths.tenant_user_sandbox_user_data_dir("t", "u", "th").resolve()

    def test_traversal_rejected(self, paths):
        paths.ensure_tenant_user_thread_dirs("t", "u", "th")
        with pytest.raises(ValueError, match="traversal"):
            paths.resolve_tenant_user_virtual_path("t", "u", "th", "/mnt/user-data/../../etc/passwd")

    def test_prefix_confusion_rejected(self, paths):
        with pytest.raises(ValueError, match="must start with"):
            paths.resolve_tenant_user_virtual_path("t", "u", "th", "/mnt/user-dataX/file")

    def test_wrong_prefix_rejected(self, paths):
        with pytest.raises(ValueError, match="must start with"):
            paths.resolve_tenant_user_virtual_path("t", "u", "th", "/some/other/path")


class TestSandboxStateDir:
    def test_basic_path(self, paths, tmp_path):
        p = paths.sandbox_state_dir("th-1")
        assert p == tmp_path / "sandbox_state" / "th-1"

    def test_independent_of_tenant_user(self, paths, tmp_path):
        """sandbox_state_dir only needs thread_id, not tenant/user."""
        p = paths.sandbox_state_dir("th-1")
        assert "tenants" not in str(p)
        assert "users" not in str(p)

    def test_invalid_thread_id(self, paths):
        with pytest.raises(ValueError, match="Invalid thread_id"):
            paths.sandbox_state_dir("../bad")


class TestCtxConvenience:
    def test_thread_dir_ctx(self, paths, tmp_path):
        from src.gateway.thread_context import ThreadContext
        ctx = ThreadContext(tenant_id="t", user_id="u", thread_id="th")
        assert paths.thread_dir_ctx(ctx) == paths.tenant_user_thread_dir("t", "u", "th")

    def test_sandbox_work_dir_ctx(self, paths):
        from src.gateway.thread_context import ThreadContext
        ctx = ThreadContext(tenant_id="t", user_id="u", thread_id="th")
        assert paths.sandbox_work_dir_ctx(ctx) == paths.tenant_user_sandbox_work_dir("t", "u", "th")

    def test_ensure_thread_dirs_ctx(self, paths):
        from src.gateway.thread_context import ThreadContext
        ctx = ThreadContext(tenant_id="t", user_id="u", thread_id="th")
        paths.ensure_thread_dirs_ctx(ctx)
        assert paths.sandbox_work_dir_ctx(ctx).is_dir()


class TestDeprecatedMethodsStillWork:
    """Ensure old flat-path methods still work during migration."""

    def test_thread_dir(self, paths, tmp_path):
        p = paths.thread_dir("th-1")
        assert p == tmp_path / "threads" / "th-1"

    def test_sandbox_work_dir(self, paths):
        p = paths.sandbox_work_dir("th-1")
        assert "workspace" in str(p)

    def test_ensure_thread_dirs(self, paths):
        paths.ensure_thread_dirs("th-1")
        assert paths.sandbox_work_dir("th-1").is_dir()
