"""Tests for the lead-agent ``<identity authoritative="true">`` anchor.

Behaviour we lock in:

- When there is no authenticated user, no ``<identity>`` block is emitted —
  we never fabricate placeholder identity text, or the model could latch onto
  it and blindly populate tool fields from the transcript.
- When an authenticated user is present, the block contains the name and
  safe user id, plus ``auth_employee_no`` when available.
- The accompanying prose spells out the never-override fields (``caller``,
  ``employeeNo``, etc.) so the model treats them as system-managed.
- Both dict-shaped and object-shaped ``auth_user`` inputs work (the wire
  format from ``config.configurable`` is a dict; direct Python calls pass
  an ``AuthenticatedUser``-like object).
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agents.lead_agent.prompt import (
    _render_identity_anchor,
    apply_prompt_template,
)


def test_anchor_empty_when_auth_user_is_none():
    assert _render_identity_anchor(None) == ""


def test_anchor_empty_when_user_id_missing():
    """Defence in depth: dict present but no ``user_id`` — refuse to render."""
    assert _render_identity_anchor({"name": "Alice"}) == ""


def test_anchor_renders_dict_auth_user():
    out = _render_identity_anchor(
        {"name": "Alice", "user_id": "u_ABC", "employee_no": "E0001"}
    )
    assert "<identity authoritative=\"true\">" in out
    assert "</identity>" in out
    assert "auth_user_name: Alice" in out
    assert "auth_user_id: u_ABC" in out
    assert "auth_employee_no: E0001" in out
    # Prose must name the fields a model might try to overwrite.
    for forbidden_field in ("caller", "employeeNo", "userId", "organizer"):
        assert forbidden_field in out


def test_anchor_renders_object_auth_user():
    user = SimpleNamespace(name="Bob", user_id="u_XYZ", employee_no=None)
    out = _render_identity_anchor(user)
    assert "auth_user_name: Bob" in out
    assert "auth_user_id: u_XYZ" in out
    # Missing employee_no should simply be omitted, not rendered as empty.
    assert "auth_employee_no" not in out


def test_apply_prompt_template_injects_anchor():
    prompt = apply_prompt_template(
        auth_user={"name": "Alice", "user_id": "u_ABC", "employee_no": "E0001"},
    )
    assert "<identity authoritative=\"true\">" in prompt
    assert "auth_user_id: u_ABC" in prompt


def test_apply_prompt_template_no_anchor_without_auth_user():
    prompt = apply_prompt_template()
    assert "<identity authoritative=\"true\">" not in prompt
