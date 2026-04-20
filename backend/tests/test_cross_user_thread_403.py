"""Regression: user A's ``df_session`` cannot access user B's thread.

This is the single most important invariant the SSO work must preserve — a
cookie that authenticates ``user_a`` must never satisfy ownership checks on a
thread whose binding was registered for ``user_b``.

The ownership gate lives in :func:`resolve_thread_context`; every HTTP router
that touches thread-scoped resources (runtime, uploads, artifacts, me,
skills) funnels through it. Testing that gate directly gives us confidence
over every caller without spinning up each router.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import src.config.paths as paths_mod
from src.gateway import thread_registry as thread_registry_mod
from src.gateway.thread_context import resolve_thread_context
from src.gateway.thread_registry import ThreadRegistry


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)
    # Force a fresh ThreadRegistry instance pointed at the temp home.
    registry = ThreadRegistry()
    monkeypatch.setattr(thread_registry_mod, "_thread_registry", registry)
    monkeypatch.setattr(thread_registry_mod, "get_thread_registry", lambda: registry)
    yield registry
    monkeypatch.setattr(paths_mod, "_paths", None)


def _register(
    registry: ThreadRegistry,
    *,
    thread_id: str,
    tenant: str,
    user: str,
) -> None:
    registry.register_binding(
        thread_id,
        tenant_id=tenant,
        user_id=user,
        portal_session_id=f"portal_{thread_id}",
    )


def test_owner_passes(isolated_registry):
    _register(isolated_registry, thread_id="t_owner", tenant="moss-hub", user="u_ABC")
    ctx = resolve_thread_context("t_owner", "moss-hub", "u_ABC")
    assert ctx.user_id == "u_ABC"


def test_different_user_same_tenant_403(isolated_registry):
    _register(isolated_registry, thread_id="t_shared", tenant="moss-hub", user="u_ABC")

    with pytest.raises(HTTPException) as excinfo:
        resolve_thread_context("t_shared", "moss-hub", "u_XYZ")

    assert excinfo.value.status_code == 403


def test_different_tenant_403(isolated_registry):
    _register(isolated_registry, thread_id="t_cross", tenant="moss-hub", user="u_ABC")

    with pytest.raises(HTTPException) as excinfo:
        resolve_thread_context("t_cross", "other-tenant", "u_ABC")

    assert excinfo.value.status_code == 403


def test_unknown_thread_403_not_404(isolated_registry):
    """Unknown thread ids return 403 (not 404) to prevent enumeration."""
    with pytest.raises(HTTPException) as excinfo:
        resolve_thread_context("t_nonexistent", "moss-hub", "u_ABC")

    assert excinfo.value.status_code == 403


def test_legacy_binding_denied_under_oidc(isolated_registry, monkeypatch):
    """A binding created without a ``user_id`` must be rejected once OIDC is on."""
    # Simulate a legacy binding with ``user_id=None`` — go around the
    # register_binding safety net that would normally coerce to a string.
    monkeypatch.setattr(
        isolated_registry,
        "get_binding",
        lambda tid: {"tenant_id": "moss-hub", "user_id": None}
        if tid == "t_legacy"
        else None,
    )
    monkeypatch.setenv("OIDC_ENABLED", "true")

    with pytest.raises(HTTPException) as excinfo:
        resolve_thread_context("t_legacy", "moss-hub", "u_ABC")

    assert excinfo.value.status_code == 403


def test_invalid_thread_id_400(isolated_registry):
    with pytest.raises(HTTPException) as excinfo:
        resolve_thread_context("../etc/passwd", "moss-hub", "u_ABC")

    assert excinfo.value.status_code == 400
