"""Tests for ``src.gateway.sso.config.load_sso_config`` fail-fast."""

from __future__ import annotations

import pytest

from src.gateway.sso import config as sso_config


_SECRET = "x" * 32
_OTHER_SECRET = "y" * 32


@pytest.fixture(autouse=True)
def _reset_cache():
    sso_config.reset_sso_config_cache()
    yield
    sso_config.reset_sso_config_cache()


def _set_enabled_env(monkeypatch, **overrides):
    base = {
        "SSO_ENABLED": "true",
        "MOSS_HUB_BASE_URL": "https://moss-hub.example/",
        "MOSS_HUB_APP_KEY": "app-key",
        "MOSS_HUB_APP_SECRET": _SECRET,
        "DEERFLOW_JWT_SECRET": _OTHER_SECRET,
        "ENVIRONMENT": "dev",
    }
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SSO_ENABLED", raising=False)
    cfg = sso_config.load_sso_config()
    assert cfg.enabled is False


def test_enabled_valid(monkeypatch):
    _set_enabled_env(monkeypatch)
    cfg = sso_config.load_sso_config()
    assert cfg.enabled is True
    # Trailing slash stripped
    assert cfg.moss_hub_base_url == "https://moss-hub.example"
    assert cfg.tenant_id == "moss-hub"
    assert cfg.cookie_name == "df_session"
    assert cfg.jwt_ttl == 28800


def test_enabled_missing_required_raises(monkeypatch):
    _set_enabled_env(monkeypatch, MOSS_HUB_APP_KEY="")
    with pytest.raises(sso_config.SSOConfigError, match="MOSS_HUB_APP_KEY"):
        sso_config.load_sso_config()


def test_short_app_secret_rejected(monkeypatch):
    _set_enabled_env(monkeypatch, MOSS_HUB_APP_SECRET="tooshort")
    with pytest.raises(sso_config.SSOConfigError, match="MOSS_HUB_APP_SECRET"):
        sso_config.load_sso_config()


def test_short_jwt_secret_rejected(monkeypatch):
    _set_enabled_env(monkeypatch, DEERFLOW_JWT_SECRET="tooshort")
    with pytest.raises(sso_config.SSOConfigError, match="DEERFLOW_JWT_SECRET"):
        sso_config.load_sso_config()


def test_insecure_cookie_in_prod_rejected(monkeypatch):
    _set_enabled_env(
        monkeypatch,
        ENVIRONMENT="production",
        SSO_COOKIE_SECURE="false",
    )
    with pytest.raises(sso_config.SSOConfigError, match="SSO_COOKIE_SECURE"):
        sso_config.load_sso_config()


def test_insecure_cookie_allowed_in_dev(monkeypatch):
    _set_enabled_env(
        monkeypatch,
        ENVIRONMENT="dev",
        SSO_COOKIE_SECURE="false",
    )
    cfg = sso_config.load_sso_config()
    assert cfg.cookie_secure is False


def test_callback_exempt_path_present(monkeypatch):
    monkeypatch.delenv("SSO_ENABLED", raising=False)
    cfg = sso_config.load_sso_config()
    assert "/api/sso/callback" in cfg.exempt_paths
