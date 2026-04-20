"""Tests for ``src.gateway.sso.user_id.derive_safe_user_id``."""

from __future__ import annotations

import re

import pytest

from src.gateway.sso.user_id import derive_safe_user_id


_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def test_derive_is_deterministic():
    assert derive_safe_user_id("10086") == derive_safe_user_id("10086")


def test_derive_distinguishes_inputs():
    assert derive_safe_user_id("10086") != derive_safe_user_id("10087")


def test_derive_output_is_path_safe_and_has_prefix():
    out = derive_safe_user_id("alice@example.com")
    assert out.startswith("u_")
    assert _SAFE_RE.match(out)
    # 2-char prefix + 24 base32 chars = 26
    assert len(out) == 26


def test_derive_strips_whitespace():
    assert derive_safe_user_id("  alice  ") == derive_safe_user_id("alice")


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_derive_rejects_empty(bad):
    with pytest.raises(ValueError):
        derive_safe_user_id(bad)
