"""Tests for ``src.gateway.sso.user_provisioning.upsert_user_md``."""

from __future__ import annotations

import yaml

import pytest

import src.config.paths as paths_mod
from src.gateway.sso.models import ProvisionedSsoUser
from src.gateway.sso.user_provisioning import upsert_user_md


@pytest.fixture
def paths_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)
    yield tmp_path
    monkeypatch.setattr(paths_mod, "_paths", None)


def _user(**overrides):
    base = dict(
        tenant_id="moss-hub",
        safe_user_id="u_ABCDEFGHIJKLMNOPQRSTUVWX",
        raw_user_id="10086",
        employee_no="E0001",
        name="Alice",
        target_system="luliu",
    )
    base.update(overrides)
    return ProvisionedSsoUser(**base)


def _parse(path):
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---\n"), "USER.md must begin with frontmatter"
    _, fm, body = raw.split("---", 2)
    return yaml.safe_load(fm), body


def test_first_write_creates_frontmatter(paths_root):
    path = upsert_user_md(_user())
    assert path.exists()
    fm, body = _parse(path)
    assert fm["user_id"] == "u_ABCDEFGHIJKLMNOPQRSTUVWX"
    assert fm["raw_user_id"] == "10086"
    assert fm["employee_no"] == "E0001"
    assert fm["name"] == "Alice"
    assert fm["tenant_id"] == "moss-hub"
    assert fm["target_system"] == "luliu"
    assert fm["source"] == "moss-hub-sso"
    assert fm["first_login_at"] == fm["last_login_at"]
    # Body empty on first write
    assert body.strip() == ""


def test_second_write_preserves_first_login_and_body(paths_root):
    first = upsert_user_md(_user())
    fm1, _ = _parse(first)

    # Append a body paragraph outside the frontmatter
    raw = first.read_text(encoding="utf-8")
    first.write_text(raw + "\n# Notes\nHello world\n", encoding="utf-8")

    # Second upsert with a mutable field change
    upsert_user_md(_user(name="Alice B"))

    fm2, body2 = _parse(first)
    assert fm2["first_login_at"] == fm1["first_login_at"]
    assert fm2["last_login_at"] >= fm1["last_login_at"]
    assert fm2["name"] == "Alice B"
    assert "Hello world" in body2


def test_preserves_unknown_frontmatter_keys(paths_root):
    path = upsert_user_md(_user())
    raw = path.read_text(encoding="utf-8")
    # Inject a custom key inside the frontmatter
    raw = raw.replace("source: moss-hub-sso\n", "source: moss-hub-sso\ncustom_flag: keep-me\n")
    path.write_text(raw, encoding="utf-8")

    upsert_user_md(_user())
    fm, _ = _parse(path)
    assert fm.get("custom_flag") == "keep-me"


def test_path_uses_tenant_and_safe_user_id(paths_root):
    path = upsert_user_md(_user())
    parts = path.parts
    assert "tenants" in parts
    assert "moss-hub" in parts
    assert "u_ABCDEFGHIJKLMNOPQRSTUVWX" in parts
    assert path.name == "USER.md"
