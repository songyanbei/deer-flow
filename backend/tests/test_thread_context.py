"""Tests for ThreadContext — dataclass, serialization, and resolve factories."""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.gateway.thread_context import (
    ThreadContext,
    resolve_thread_context,
    resolve_thread_context_lenient,
)
from src.gateway.thread_registry import ThreadRegistry

REGISTRY_PATCH = "src.gateway.thread_registry.get_thread_registry"


@pytest.fixture
def registry(tmp_path):
    return ThreadRegistry(registry_file=tmp_path / "registry.json")


# ── Dataclass basics ──────────────────────────────────────────────────


class TestThreadContext:
    def test_frozen(self):
        ctx = ThreadContext(tenant_id="t", user_id="u", thread_id="th")
        with pytest.raises(AttributeError):
            ctx.tenant_id = "x"

    def test_serialize_roundtrip(self):
        ctx = ThreadContext(tenant_id="acme", user_id="alice", thread_id="abc-123")
        d = ctx.serialize()
        assert d == {"tenant_id": "acme", "user_id": "alice", "thread_id": "abc-123"}
        restored = ThreadContext.deserialize(d)
        assert restored == ctx

    def test_deserialize_coerces_to_str(self):
        d = {"tenant_id": 123, "user_id": 456, "thread_id": 789}
        ctx = ThreadContext.deserialize(d)
        assert ctx.tenant_id == "123"
        assert ctx.user_id == "456"
        assert ctx.thread_id == "789"


# ── resolve_thread_context (HTTP layer) ───────────────────────────────


class TestResolveThreadContext:
    def test_success(self, registry):
        registry.register("th-1", "tenant-a", user_id="user-1")
        with patch(REGISTRY_PATCH, return_value=registry):
            ctx = resolve_thread_context("th-1", "tenant-a", "user-1")
        assert ctx.tenant_id == "tenant-a"
        assert ctx.user_id == "user-1"
        assert ctx.thread_id == "th-1"

    def test_unknown_thread_returns_403(self, registry):
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(HTTPException) as exc_info:
                resolve_thread_context("nonexistent", "tenant-a", "user-1")
            assert exc_info.value.status_code == 403

    def test_tenant_mismatch_returns_403(self, registry):
        registry.register("th-1", "tenant-a", user_id="user-1")
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(HTTPException) as exc_info:
                resolve_thread_context("th-1", "tenant-b", "user-1")
            assert exc_info.value.status_code == 403

    def test_user_mismatch_returns_403(self, registry):
        registry.register("th-1", "tenant-a", user_id="user-1")
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(HTTPException) as exc_info:
                resolve_thread_context("th-1", "tenant-a", "user-2")
            assert exc_info.value.status_code == 403

    def test_invalid_thread_id_returns_400(self, registry):
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(HTTPException) as exc_info:
                resolve_thread_context("../escape", "tenant-a", "user-1")
            assert exc_info.value.status_code == 400

    def test_invalid_tenant_id_returns_400(self, registry):
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(HTTPException) as exc_info:
                resolve_thread_context("th-1", "tenant/bad", "user-1")
            assert exc_info.value.status_code == 400

    def test_oidc_enabled_legacy_entry_without_user_returns_403(self, registry):
        registry.register("th-1", "tenant-a", user_id=None)
        with (
            patch(REGISTRY_PATCH, return_value=registry),
            patch.dict(os.environ, {"OIDC_ENABLED": "true"}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                resolve_thread_context("th-1", "tenant-a", "user-1")
            assert exc_info.value.status_code == 403

    def test_oidc_disabled_legacy_entry_without_user_allowed(self, registry):
        registry.register("th-1", "tenant-a", user_id=None)
        with (
            patch(REGISTRY_PATCH, return_value=registry),
            patch.dict(os.environ, {"OIDC_ENABLED": "false"}),
        ):
            ctx = resolve_thread_context("th-1", "tenant-a", "user-1")
            assert ctx.thread_id == "th-1"

    def test_tenant_id_always_present_in_sqlite(self, registry):
        """SQLite schema enforces NOT NULL on tenant_id — edge case structurally prevented."""
        import sqlite3
        registry.register("th-1", "tenant-a", user_id="user-1")
        with registry._lock:
            conn = registry._get_conn()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE threads SET tenant_id = NULL WHERE thread_id = ?", ("th-1",))
            conn.rollback()


# ── resolve_thread_context_lenient (non-HTTP layer) ──────────────────


class TestResolveThreadContextLenient:
    def test_success(self, registry):
        registry.register("th-1", "tenant-a", user_id="user-1")
        with patch(REGISTRY_PATCH, return_value=registry):
            ctx = resolve_thread_context_lenient("th-1", "tenant-a", "user-1")
        assert ctx == ThreadContext(tenant_id="tenant-a", user_id="user-1", thread_id="th-1")

    def test_unknown_thread_raises_value_error(self, registry):
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(ValueError, match="not found"):
                resolve_thread_context_lenient("nonexistent", "tenant-a", "user-1")

    def test_tenant_mismatch_raises_value_error(self, registry):
        registry.register("th-1", "tenant-a", user_id="user-1")
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(ValueError, match="Tenant mismatch"):
                resolve_thread_context_lenient("th-1", "tenant-b", "user-1")

    def test_user_mismatch_raises_value_error(self, registry):
        registry.register("th-1", "tenant-a", user_id="user-1")
        with patch(REGISTRY_PATCH, return_value=registry):
            with pytest.raises(ValueError, match="User mismatch"):
                resolve_thread_context_lenient("th-1", "tenant-a", "user-2")

    def test_invalid_ids_raise_value_error(self, registry):
        with pytest.raises(ValueError, match="Invalid"):
            resolve_thread_context_lenient("../bad", "tenant-a", "user-1")
