"""Tests for ``src.gateway.sso.jwt_signer``."""

from __future__ import annotations

import time

import pytest
from jose import JWTError, jwt

from src.gateway.sso.config import SSOConfig
from src.gateway.sso.jwt_signer import (
    INTERNAL_ALG,
    INTERNAL_ISS,
    INTERNAL_KID,
    is_internal_token,
    sign_df_session,
    verify_df_session,
)
from src.gateway.sso.models import ProvisionedSsoUser


def _cfg(secret: str = "a" * 40, ttl: int = 3600) -> SSOConfig:
    return SSOConfig(
        enabled=True,
        moss_hub_base_url="https://moss.example",
        moss_hub_app_key="k",
        moss_hub_app_secret="s" * 40,
        jwt_secret=secret,
        jwt_ttl=ttl,
    )


def _user() -> ProvisionedSsoUser:
    return ProvisionedSsoUser(
        tenant_id="moss-hub",
        safe_user_id="u_ABCDEFGHIJKLMNOPQRSTUVWX",
        raw_user_id="10086",
        employee_no="E0001",
        name="Alice",
        target_system="luliu",
    )


def test_sign_and_verify_roundtrip():
    cfg = _cfg()
    token = sign_df_session(_user(), config=cfg)
    claims = verify_df_session(token, config=cfg)
    assert claims["sub"] == "u_ABCDEFGHIJKLMNOPQRSTUVWX"
    assert claims["iss"] == INTERNAL_ISS
    assert claims["tenant_id"] == "moss-hub"
    assert claims["employee_no"] == "E0001"
    assert claims["target_system"] == "luliu"
    assert claims["role"] == "member"
    assert claims["exp"] - claims["iat"] == cfg.jwt_ttl


def test_header_has_kid_and_alg():
    cfg = _cfg()
    token = sign_df_session(_user(), config=cfg)
    header = jwt.get_unverified_header(token)
    assert header["kid"] == INTERNAL_KID
    assert header["alg"] == INTERNAL_ALG


def test_is_internal_token():
    cfg = _cfg()
    token = sign_df_session(_user(), config=cfg)
    assert is_internal_token(token) is True
    # Foreign token with another kid
    other = jwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": "other"})
    assert is_internal_token(other) is False
    assert is_internal_token("not.a.jwt") is False


def test_verify_rejects_wrong_kid():
    cfg = _cfg()
    bad = jwt.encode(
        {"iss": INTERNAL_ISS, "sub": "x", "exp": int(time.time()) + 60},
        cfg.jwt_secret,
        algorithm="HS256",
        headers={"kid": "external-kid"},
    )
    with pytest.raises(JWTError, match="kid"):
        verify_df_session(bad, config=cfg)


def test_verify_rejects_wrong_alg():
    # HS384 instead of HS256
    cfg = _cfg()
    bad = jwt.encode(
        {"iss": INTERNAL_ISS, "sub": "x", "exp": int(time.time()) + 60},
        cfg.jwt_secret,
        algorithm="HS384",
        headers={"kid": INTERNAL_KID},
    )
    with pytest.raises(JWTError, match="alg"):
        verify_df_session(bad, config=cfg)


def test_verify_rejects_expired():
    cfg = _cfg(ttl=1)
    token = sign_df_session(_user(), config=cfg, now=time.time() - 3600)
    with pytest.raises(JWTError):
        verify_df_session(token, config=cfg)


def test_verify_rejects_wrong_issuer():
    cfg = _cfg()
    bad = jwt.encode(
        {"iss": "someone-else", "sub": "x", "exp": int(time.time()) + 60},
        cfg.jwt_secret,
        algorithm="HS256",
        headers={"kid": INTERNAL_KID},
    )
    with pytest.raises(JWTError):
        verify_df_session(bad, config=cfg)


def test_sign_requires_secret():
    cfg = SSOConfig(enabled=True, jwt_secret="")
    with pytest.raises(RuntimeError, match="DEERFLOW_JWT_SECRET"):
        sign_df_session(_user(), config=cfg)
