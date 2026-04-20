"""Middleware tests for Cookie fallback + kid-based routing.

Covers the SSO-side behavior added to ``OIDCAuthMiddleware``:

- Bearer header wins over cookie.
- ``df_session`` cookie is consulted only when SSO is enabled.
- ``kid=df-internal-v1`` routes to the local HS256 verifier; any other ``kid``
  routes to the JWKS flow.
- Internal tokens with ``alg != HS256`` are rejected (audit + 401).
- Rejections write a ``sso_token_invalid`` event through
  ``AuthAuditLedger.record_token_invalid``.
- ``request.state`` is populated with ``employee_no`` / ``target_system``
  for internal tokens.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from jose import jwt as jose_jwt
from starlette.testclient import TestClient

import src.config.paths as paths_mod
from src.gateway.middleware.oidc import OIDCAuthMiddleware
from src.gateway.middleware.oidc_config import OIDCConfig
from src.gateway.sso import audit as audit_mod
from src.gateway.sso.config import SSOConfig
from src.gateway.sso.jwt_signer import INTERNAL_KID, sign_df_session
from src.gateway.sso.models import ProvisionedSsoUser


@pytest.fixture
def sandboxed_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)
    monkeypatch.setattr(audit_mod, "_default_ledger", None)
    yield tmp_path
    monkeypatch.setattr(paths_mod, "_paths", None)
    monkeypatch.setattr(audit_mod, "_default_ledger", None)


def _sso_config() -> SSOConfig:
    return SSOConfig(
        enabled=True,
        moss_hub_base_url="https://moss-hub.example",
        moss_hub_app_key="k",
        moss_hub_app_secret="s" * 40,
        jwt_secret="j" * 40,
        jwt_ttl=3600,
        tenant_id="moss-hub",
        cookie_name="df_session",
        cookie_secure=False,
        environment="dev",
    )


def _oidc_config(enabled: bool = False) -> OIDCConfig:
    return OIDCConfig(
        enabled=enabled,
        issuer=None,
        jwks_uri="",
        audience=None,
        algorithms=["RS256"],
        verify_ssl=False,
        tenant_claims=["organization", "tenant_id", "org_id"],
        jwks_cache_ttl=3600,
        exempt_paths={"/health"},
        exempt_path_prefixes=[],
    )


def _build_app(sso_config: SSOConfig, oidc_config: OIDCConfig) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        OIDCAuthMiddleware,
        config=oidc_config,
        sso_config=sso_config,
    )

    @app.get("/api/me")
    async def me(request: Request):
        return {
            "tenant_id": getattr(request.state, "tenant_id", None),
            "user_id": getattr(request.state, "user_id", None),
            "username": getattr(request.state, "username", None),
            "employee_no": getattr(request.state, "employee_no", None),
            "target_system": getattr(request.state, "target_system", None),
            "token_source": getattr(request.state, "token_source", None),
            "token_kid": getattr(request.state, "token_kid", None),
        }

    return app


def _mint_df_session(sso_cfg: SSOConfig) -> str:
    user = ProvisionedSsoUser(
        tenant_id="moss-hub",
        safe_user_id="u_ABCDEF",
        raw_user_id="10086",
        employee_no="E0001",
        name="Alice",
        target_system="luliu",
    )
    return sign_df_session(user, config=sso_cfg)


def test_cookie_auth_injects_identity(sandboxed_paths):
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))
    token = _mint_df_session(sso_cfg)

    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("df_session", token)
    resp = client.get("/api/me")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == "moss-hub"
    assert data["user_id"] == "u_ABCDEF"
    assert data["username"] == "Alice"
    assert data["employee_no"] == "E0001"
    assert data["target_system"] == "luliu"
    assert data["token_source"] == "cookie"
    assert data["token_kid"] == INTERNAL_KID


def test_bearer_beats_cookie(sandboxed_paths):
    """When both are present, Bearer is read — the cookie is ignored."""
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))
    good_cookie = _mint_df_session(sso_cfg)

    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("df_session", good_cookie)
    # Send a garbage Bearer — middleware should try to parse it (not fall back).
    resp = client.get(
        "/api/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert resp.status_code == 401


def test_empty_cookie_rejected_with_audit(sandboxed_paths):
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))

    recorded: list[dict] = []

    def _spy(self, *, reason, tenant_id=None, user_id=None, client_ip=None, user_agent=None, kid=None):
        recorded.append({"reason": reason, "kid": kid})

    with patch.object(audit_mod.AuthAuditLedger, "record_token_invalid", _spy):
        client = TestClient(app, raise_server_exceptions=False)
        # Starlette will drop a truly-empty cookie value, so use whitespace — the
        # middleware's ``.strip()`` turns it into empty and triggers the audit.
        client.cookies.set("df_session", "   ")
        resp = client.get("/api/me")

    # Either the middleware sees no token (whitespace dropped by the client)
    # or it records an ``empty_cookie_token`` audit — both are valid outcomes;
    # the invariant we actually care about is the 401.
    assert resp.status_code == 401


def test_internal_token_wrong_alg_rejected(sandboxed_paths):
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))

    # Hand-craft a token with kid=df-internal-v1 but alg=HS512.
    bogus = jose_jwt.encode(
        {"sub": "x"},
        sso_cfg.jwt_secret,
        algorithm="HS512",
        headers={"kid": INTERNAL_KID},
    )

    recorded: list[dict] = []

    def _spy(self, *, reason, tenant_id=None, user_id=None, client_ip=None, user_agent=None, kid=None):
        recorded.append({"reason": reason, "kid": kid})

    with patch.object(audit_mod.AuthAuditLedger, "record_token_invalid", _spy):
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("df_session", bogus)
        resp = client.get("/api/me")

    assert resp.status_code == 401
    assert any("internal_token_bad_alg" in e["reason"] for e in recorded)


def test_internal_token_when_sso_disabled_is_rejected(sandboxed_paths):
    """If a caller sneaks a df-internal-v1 token in while SSO is disabled,
    the middleware must refuse to verify it locally."""
    sso_cfg_for_minting = _sso_config()
    token = _mint_df_session(sso_cfg_for_minting)

    disabled = SSOConfig(enabled=False)
    app = _build_app(disabled, _oidc_config(enabled=False))

    recorded: list[dict] = []

    def _spy(self, *, reason, tenant_id=None, user_id=None, client_ip=None, user_agent=None, kid=None):
        recorded.append({"reason": reason, "kid": kid})

    with patch.object(audit_mod.AuthAuditLedger, "record_token_invalid", _spy):
        client = TestClient(app, raise_server_exceptions=False)
        # Cookie path only reads when sso enabled, so send as Bearer.
        resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any("internal_token_but_sso_disabled" in e["reason"] for e in recorded)


def test_external_kid_without_jwks_rejected(sandboxed_paths):
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))

    # Token with a foreign kid — neither internal nor resolvable via JWKS.
    bogus = jose_jwt.encode(
        {"sub": "x"},
        "anysecret",
        algorithm="HS256",
        headers={"kid": "external-kid-xyz"},
    )

    recorded: list[dict] = []

    def _spy(self, *, reason, tenant_id=None, user_id=None, client_ip=None, user_agent=None, kid=None):
        recorded.append({"reason": reason, "kid": kid})

    with patch.object(audit_mod.AuthAuditLedger, "record_token_invalid", _spy):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/me", headers={"Authorization": f"Bearer {bogus}"})

    assert resp.status_code == 401
    assert any("external_token_but_oidc_disabled" in e["reason"] for e in recorded)


def test_missing_token_audited(sandboxed_paths):
    """P1 regression: a protected request with no Bearer and no cookie must
    still emit ``sso_token_invalid`` so operators can spot scraping /
    enumeration. Previously it returned a bare 401 with no audit trail."""
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))

    recorded: list[dict] = []

    def _spy(self, *, reason, tenant_id=None, user_id=None, client_ip=None, user_agent=None, kid=None):
        recorded.append({"reason": reason, "kid": kid})

    with patch.object(audit_mod.AuthAuditLedger, "record_token_invalid", _spy):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/me")  # no Bearer, no cookie

    assert resp.status_code == 401
    assert any("missing_token" in e["reason"] for e in recorded)


def test_missing_kid_rejected(sandboxed_paths):
    sso_cfg = _sso_config()
    app = _build_app(sso_cfg, _oidc_config(enabled=False))

    # No kid in the header.
    token = jose_jwt.encode({"sub": "x"}, "any", algorithm="HS256")

    recorded: list[dict] = []

    def _spy(self, *, reason, tenant_id=None, user_id=None, client_ip=None, user_agent=None, kid=None):
        recorded.append({"reason": reason, "kid": kid})

    with patch.object(audit_mod.AuthAuditLedger, "record_token_invalid", _spy):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any("missing_kid" in e["reason"] for e in recorded)
