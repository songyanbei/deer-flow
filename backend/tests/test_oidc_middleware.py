"""Tests for OIDC authentication middleware and configuration.

Covers:
- OIDCConfig loading from environment variables
- JWT verification with mock JWKS
- Tenant claim extraction from various JWT payload shapes
- Middleware integration (401, 403, exempt paths, valid tokens)
- JWKS cache TTL and key-rotation refresh
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt as jose_jwt
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from src.gateway.middleware.oidc import OIDCAuthMiddleware, _JWKSCache, _extract_tenant_id, _verify_token
from src.gateway.middleware.oidc_config import OIDCConfig, load_oidc_config

# ── Test RSA key pair for signing JWTs ──────────────────────────────────
# Generated deterministically for testing only. Never use in production.

try:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    _private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    _private_pem = _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    _public_key = _private_key.public_key()
    _public_numbers = _public_key.public_numbers()

    import base64

    def _int_to_base64url(n: int, length: int | None = None) -> str:
        byte_length = length or (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(byte_length, byteorder="big")).rstrip(b"=").decode()

    _TEST_KID = "test-kid-001"
    _TEST_JWKS = {
        "keys": [
            {
                "kty": "RSA",
                "kid": _TEST_KID,
                "use": "sig",
                "alg": "RS256",
                "n": _int_to_base64url(_public_numbers.n, 256),
                "e": _int_to_base64url(_public_numbers.e, 3),
            }
        ]
    }
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False
    _private_pem = b""
    _TEST_KID = ""
    _TEST_JWKS = {"keys": []}

# ── Helpers ─────────────────────────────────────────────────────────────

_TEST_ISSUER = "https://auth.test.local/realms/test"
_TEST_AUDIENCE = "test-client"


def _make_token(claims: dict, kid: str = _TEST_KID) -> str:
    """Create a signed JWT for testing."""
    headers = {"kid": kid, "alg": "RS256"}
    return jose_jwt.encode(claims, _private_pem, algorithm="RS256", headers=headers)


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    base = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "sub": "user-123",
        "exp": now + 3600,
        "iat": now,
        "preferred_username": "testuser",
    }
    base.update(overrides)
    return base


def _make_config(**overrides) -> OIDCConfig:
    defaults = {
        "enabled": True,
        "issuer": _TEST_ISSUER,
        "jwks_uri": "https://auth.test.local/jwks",
        "audience": _TEST_AUDIENCE,
        "algorithms": ["RS256"],
        "verify_ssl": False,
        "tenant_claims": ["organization", "tenant_id", "org_id"],
        "jwks_cache_ttl": 3600,
        "exempt_paths": {"/health", "/docs", "/redoc", "/openapi.json", "/debug/metrics"},
        "exempt_path_prefixes": ["/docs", "/redoc"],
    }
    defaults.update(overrides)
    return OIDCConfig(**defaults)


# ── OIDCConfig loading tests ───────────────────────────────────────────


class TestOIDCConfigLoading:
    """Tests for ``load_oidc_config()`` environment variable parsing."""

    def test_defaults_when_no_env_vars(self):
        with patch.dict("os.environ", {}, clear=True):
            config = load_oidc_config()
        assert config.enabled is False
        assert config.issuer is None
        assert config.algorithms == ["RS256"]
        assert config.jwks_cache_ttl == 3600
        assert "/health" in config.exempt_paths

    def test_enabled_from_env(self):
        env = {"OIDC_ENABLED": "true", "OIDC_ISSUER": "https://auth.example.com/realms/test"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.enabled is True
        assert config.issuer == "https://auth.example.com/realms/test"

    def test_jwks_uri_auto_discovery(self):
        env = {"OIDC_ISSUER": "https://auth.example.com/realms/test"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.jwks_uri == "https://auth.example.com/realms/test/protocol/openid-connect/certs"

    def test_jwks_uri_explicit_override(self):
        env = {
            "OIDC_ISSUER": "https://auth.example.com/realms/test",
            "OIDC_JWKS_URI": "https://custom-jwks.example.com/keys",
        }
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.jwks_uri == "https://custom-jwks.example.com/keys"

    def test_audience_from_env(self):
        env = {"OIDC_AUDIENCE": "my-app"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.audience == "my-app"

    def test_verify_ssl_false(self):
        env = {"OIDC_VERIFY_SSL": "false"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.verify_ssl is False

    def test_tenant_claims_from_env(self):
        env = {"OIDC_TENANT_CLAIMS": "realm,custom_tenant"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.tenant_claims == ["realm", "custom_tenant"]

    def test_custom_exempt_paths(self):
        env = {"OIDC_EXEMPT_PATHS": "/custom-health,/internal/status"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert "/custom-health" in config.exempt_paths
        assert "/health" in config.exempt_paths  # defaults preserved

    def test_algorithms_from_env(self):
        env = {"OIDC_ALGORITHMS": "RS256,RS384,ES256"}
        with patch.dict("os.environ", env, clear=True):
            config = load_oidc_config()
        assert config.algorithms == ["RS256", "RS384", "ES256"]


# ── Tenant claim extraction tests ──────────────────────────────────────


class TestTenantClaimExtraction:
    """Tests for ``_extract_tenant_id()`` with various claim shapes."""

    def test_string_value(self):
        claims = {"organization": "acme-corp"}
        assert _extract_tenant_id(claims, ["organization"]) == "acme-corp"

    def test_list_value_takes_first(self):
        claims = {"organization": ["org-a", "org-b"]}
        assert _extract_tenant_id(claims, ["organization"]) == "org-a"

    def test_dict_value_takes_first_key(self):
        # Keycloak nested org structure
        claims = {"organization": {"acme-corp": {"roles": ["admin"]}}}
        assert _extract_tenant_id(claims, ["organization"]) == "acme-corp"

    def test_fallback_priority(self):
        claims = {"org_id": "fallback-org"}
        assert _extract_tenant_id(claims, ["organization", "tenant_id", "org_id"]) == "fallback-org"

    def test_none_when_no_claim(self):
        claims = {"sub": "user-123"}
        assert _extract_tenant_id(claims, ["organization", "tenant_id"]) is None

    def test_empty_string_skipped(self):
        claims = {"organization": "", "tenant_id": "real-tenant"}
        assert _extract_tenant_id(claims, ["organization", "tenant_id"]) == "real-tenant"

    def test_empty_list_skipped(self):
        claims = {"organization": [], "tenant_id": "real-tenant"}
        assert _extract_tenant_id(claims, ["organization", "tenant_id"]) == "real-tenant"

    def test_none_value_skipped(self):
        claims = {"organization": None, "tenant_id": "real-tenant"}
        assert _extract_tenant_id(claims, ["organization", "tenant_id"]) == "real-tenant"


# ── JWKS cache tests ───────────────────────────────────────────────────


class TestJWKSCache:
    """Tests for ``_JWKSCache`` TTL, refresh, and key lookup."""

    def test_cache_returns_keys(self):
        cache = _JWKSCache("https://fake/jwks", verify_ssl=False)
        cache._keys = _TEST_JWKS["keys"]
        cache._fetched_at = time.time()
        assert len(cache.get_keys()) == 1

    def test_cache_expired_triggers_refresh(self):
        cache = _JWKSCache("https://fake/jwks", ttl=0, verify_ssl=False)
        cache._keys = [{"kid": "old"}]
        cache._fetched_at = time.time() - 10

        with patch.object(cache, "_fetch", return_value=_TEST_JWKS["keys"]) as mock_fetch:
            keys = cache.get_keys()
            mock_fetch.assert_called_once()
            assert keys == _TEST_JWKS["keys"]

    def test_find_key_success(self):
        cache = _JWKSCache("https://fake/jwks", verify_ssl=False)
        cache._keys = _TEST_JWKS["keys"]
        cache._fetched_at = time.time()
        key = cache.find_key(_TEST_KID)
        assert key["kid"] == _TEST_KID

    def test_find_key_miss_triggers_refresh(self):
        cache = _JWKSCache("https://fake/jwks", verify_ssl=False)
        cache._keys = [{"kid": "old-kid"}]
        cache._fetched_at = time.time()

        with patch.object(cache, "_fetch", return_value=_TEST_JWKS["keys"]):
            key = cache.find_key(_TEST_KID)
            assert key["kid"] == _TEST_KID

    def test_find_key_raises_on_total_miss(self):
        cache = _JWKSCache("https://fake/jwks", verify_ssl=False)
        cache._keys = [{"kid": "other"}]
        cache._fetched_at = time.time()

        with patch.object(cache, "_fetch", return_value=[{"kid": "other"}]):
            with pytest.raises(Exception, match="No matching signing key"):
                cache.find_key("nonexistent-kid")

    def test_stale_keys_used_as_fallback_on_fetch_error(self):
        cache = _JWKSCache("https://fake/jwks", ttl=0, verify_ssl=False)
        cache._keys = [{"kid": "stale"}]
        cache._fetched_at = 0

        import httpx
        with patch.object(cache, "_fetch", side_effect=httpx.ConnectError("network down")):
            keys = cache.get_keys()
            assert keys == [{"kid": "stale"}]


# ── Token verification tests ──────────────────────────────────────────


@pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography not installed")
class TestTokenVerification:
    """Tests for ``_verify_token()`` with real RSA signatures."""

    def _make_cache(self) -> _JWKSCache:
        cache = _JWKSCache("https://fake/jwks", verify_ssl=False)
        cache._keys = _TEST_JWKS["keys"]
        cache._fetched_at = time.time()
        return cache

    def test_valid_token(self):
        token = _make_token(_base_claims())
        payload = _verify_token(
            token,
            jwks_cache=self._make_cache(),
            algorithms=["RS256"],
            audience=_TEST_AUDIENCE,
            issuer=_TEST_ISSUER,
        )
        assert payload["sub"] == "user-123"
        assert payload["iss"] == _TEST_ISSUER
        assert payload["aud"] == _TEST_AUDIENCE

    def test_expired_token_rejected(self):
        token = _make_token(_base_claims(exp=int(time.time()) - 100))
        with pytest.raises(Exception):
            _verify_token(
                token,
                jwks_cache=self._make_cache(),
                algorithms=["RS256"],
                audience=_TEST_AUDIENCE,
                issuer=_TEST_ISSUER,
            )

    def test_wrong_audience_rejected(self):
        token = _make_token(_base_claims(aud="wrong-audience"))
        with pytest.raises(Exception):
            _verify_token(
                token,
                jwks_cache=self._make_cache(),
                algorithms=["RS256"],
                audience=_TEST_AUDIENCE,
                issuer=_TEST_ISSUER,
            )

    def test_wrong_issuer_rejected(self):
        token = _make_token(_base_claims(iss="https://evil.example.com"))
        with pytest.raises(Exception):
            _verify_token(
                token,
                jwks_cache=self._make_cache(),
                algorithms=["RS256"],
                audience=_TEST_AUDIENCE,
                issuer=_TEST_ISSUER,
            )

    def test_no_kid_in_header_rejected(self):
        # Manually craft a token without kid
        headers = {"alg": "RS256"}
        token = jose_jwt.encode(_base_claims(), _private_pem, algorithm="RS256", headers=headers)
        with pytest.raises(Exception, match="missing 'kid'"):
            _verify_token(
                token,
                jwks_cache=self._make_cache(),
                algorithms=["RS256"],
                audience=_TEST_AUDIENCE,
                issuer=_TEST_ISSUER,
            )

    def test_audience_validation_skipped_when_none(self):
        token = _make_token(_base_claims(aud="anything"))
        payload = _verify_token(
            token,
            jwks_cache=self._make_cache(),
            algorithms=["RS256"],
            audience=None,
            issuer=None,
        )
        assert payload["sub"] == "user-123"

    def test_token_with_tenant_claim(self):
        token = _make_token(_base_claims(organization="acme-corp"))
        payload = _verify_token(
            token,
            jwks_cache=self._make_cache(),
            algorithms=["RS256"],
            audience=_TEST_AUDIENCE,
            issuer=_TEST_ISSUER,
        )
        assert payload["organization"] == "acme-corp"


# ── Middleware integration tests ───────────────────────────────────────


@pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography not installed")
class TestOIDCMiddlewareIntegration:
    """End-to-end middleware tests using FastAPI TestClient."""

    def _create_app(self, oidc_config: OIDCConfig | None = None):
        config = oidc_config or _make_config()
        app = FastAPI()

        # Patch JWKS fetch to return test keys
        app.add_middleware(OIDCAuthMiddleware, config=config)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/models")
        async def models(request: Request):
            return {
                "tenant_id": getattr(request.state, "tenant_id", None),
                "user_id": getattr(request.state, "user_id", None),
                "username": getattr(request.state, "username", None),
            }

        return app

    def _patch_jwks(self):
        return patch.object(_JWKSCache, "_fetch", return_value=_TEST_JWKS["keys"])

    def test_no_token_returns_401(self):
        app = self._create_app()
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models")
        assert resp.status_code == 401
        assert "Missing" in resp.json()["detail"]

    def test_invalid_token_returns_401(self):
        app = self._create_app()
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": "Bearer garbage.token.here"})
        assert resp.status_code == 401

    def test_empty_bearer_returns_401(self):
        app = self._create_app()
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401
        assert "Empty" in resp.json()["detail"]

    def test_exempt_path_no_auth_required(self):
        app = self._create_app()
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_valid_token_passes_and_injects_context(self):
        app = self._create_app()
        token = _make_token(_base_claims(organization="test-org"))
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "test-org"
        assert data["user_id"] == "user-123"
        assert data["username"] == "testuser"

    def test_valid_token_without_tenant_defaults(self):
        app = self._create_app()
        token = _make_token(_base_claims())  # No org claim
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] is None

    def test_expired_token_returns_401(self):
        app = self._create_app()
        token = _make_token(_base_claims(exp=int(time.time()) - 100))
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_wrong_audience_returns_401(self):
        app = self._create_app()
        token = _make_token(_base_claims(aud="wrong-app"))
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_basic_auth_scheme_rejected(self):
        app = self._create_app()
        with self._patch_jwks():
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/models", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401
