"""Integration tests for ``POST /api/sso/callback``.

Builds a minimal FastAPI app with just the SSO router — avoids pulling the
whole gateway startup pipeline (which has a ton of unrelated side effects)
while still exercising the real request/response flow.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.config.paths as paths_mod
from src.gateway.routers import sso as sso_router
from src.gateway.sso import audit as audit_mod
from src.gateway.sso import config as sso_config
from src.gateway.sso.jwt_signer import INTERNAL_KID, verify_df_session
from src.gateway.sso.models import (
    MossHubTicketProfile,
    SsoTicketInvalid,
    SsoUpstreamError,
)


@pytest.fixture
def paths_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)
    # Reset the module-level audit ledger so it re-derives paths under tmp_path.
    monkeypatch.setattr(audit_mod, "_default_ledger", None)
    yield tmp_path
    monkeypatch.setattr(paths_mod, "_paths", None)
    monkeypatch.setattr(audit_mod, "_default_ledger", None)


@pytest.fixture
def enabled_config(monkeypatch):
    cfg = sso_config.SSOConfig(
        enabled=True,
        moss_hub_base_url="https://moss-hub.example",
        moss_hub_app_key="k",
        moss_hub_app_secret="s" * 40,
        jwt_secret="j" * 40,
        jwt_ttl=3600,
        tenant_id="moss-hub",
        cookie_name="df_session",
        cookie_secure=False,  # test client doesn't use https
        environment="dev",
    )
    monkeypatch.setattr(sso_config, "_cached", cfg)
    yield cfg
    monkeypatch.setattr(sso_config, "_cached", None)


@pytest.fixture
def client(enabled_config, paths_root):
    app = FastAPI()
    app.include_router(sso_router.router)
    return TestClient(app)


def _profile() -> MossHubTicketProfile:
    return MossHubTicketProfile(
        raw_user_id="10086",
        employee_no="E0001",
        name="Alice",
        target_system="luliu",
    )


def test_callback_happy_path(client, enabled_config, monkeypatch):
    async def fake_verify(ticket, *, config):
        assert ticket == "tkt-good"
        return _profile()

    monkeypatch.setattr(sso_router, "verify_ticket", fake_verify)

    resp = client.post("/api/sso/callback", json={"ticket": "tkt-good"})
    assert resp.status_code == 200
    assert resp.json() == {"redirect": "/workspace/chats/new"}

    cookies = resp.headers.get_list("set-cookie")
    assert any("df_session=" in c for c in cookies)
    joined = "\n".join(cookies)
    assert "HttpOnly" in joined
    assert "Max-Age=3600" in joined
    assert "SameSite=Lax" in joined or "samesite=lax" in joined.lower()

    token = client.cookies.get("df_session")
    assert token is not None
    claims = verify_df_session(token, config=enabled_config)
    assert claims["tenant_id"] == "moss-hub"
    assert claims["employee_no"] == "E0001"
    assert claims["preferred_username"] == "Alice"
    assert claims["sub"].startswith("u_")

    # Verify header kid for good measure
    from jose import jwt as jose_jwt
    assert jose_jwt.get_unverified_header(token)["kid"] == INTERNAL_KID


def test_callback_invalid_ticket_returns_401(client, monkeypatch):
    async def fake_verify(ticket, *, config):
        raise SsoTicketInvalid("moss-hub code=B002: expired")

    monkeypatch.setattr(sso_router, "verify_ticket", fake_verify)

    resp = client.post("/api/sso/callback", json={"ticket": "bad"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "login link expired"
    assert "df_session" not in resp.cookies


def test_callback_upstream_error_returns_500(client, monkeypatch):
    async def fake_verify(ticket, *, config):
        raise SsoUpstreamError("moss-hub down")

    monkeypatch.setattr(sso_router, "verify_ticket", fake_verify)

    resp = client.post("/api/sso/callback", json={"ticket": "x"})
    assert resp.status_code == 500
    assert resp.json()["detail"] == "SSO unavailable"


def test_callback_empty_ticket_rejected(client):
    resp = client.post("/api/sso/callback", json={"ticket": "   "})
    # Checklist §6: ticket-shaped failures — including missing / blank —
    # must surface as 401, not 400 or 422, so the frontend can render a
    # single "login link expired" message.
    assert resp.status_code == 401
    assert resp.json()["detail"] == "login link expired"


def test_callback_missing_ticket_field_returns_401(client):
    resp = client.post("/api/sso/callback", json={})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "login link expired"


def test_callback_sets_secure_flag_when_cookie_secure(monkeypatch, paths_root):
    cfg = sso_config.SSOConfig(
        enabled=True,
        moss_hub_base_url="https://moss-hub.example",
        moss_hub_app_key="k",
        moss_hub_app_secret="s" * 40,
        jwt_secret="j" * 40,
        jwt_ttl=3600,
        tenant_id="moss-hub",
        cookie_name="df_session",
        cookie_secure=True,  # the point of this test
        environment="dev",
    )
    monkeypatch.setattr(sso_config, "_cached", cfg)

    async def fake_verify(ticket, *, config):
        return _profile()

    monkeypatch.setattr(sso_router, "verify_ticket", fake_verify)

    app = FastAPI()
    app.include_router(sso_router.router)
    client = TestClient(app)
    resp = client.post("/api/sso/callback", json={"ticket": "tkt"})
    assert resp.status_code == 200
    cookies = resp.headers.get_list("set-cookie")
    assert any("df_session=" in c and "Secure" in c for c in cookies), cookies
    monkeypatch.setattr(sso_config, "_cached", None)


def test_callback_refuses_when_sso_disabled(monkeypatch, paths_root):
    disabled = sso_config.SSOConfig(enabled=False)
    monkeypatch.setattr(sso_config, "_cached", disabled)
    app = FastAPI()
    app.include_router(sso_router.router)
    client = TestClient(app)
    resp = client.post("/api/sso/callback", json={"ticket": "x"})
    assert resp.status_code == 500
    monkeypatch.setattr(sso_config, "_cached", None)
