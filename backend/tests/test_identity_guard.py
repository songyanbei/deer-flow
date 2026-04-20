"""Tests for ``src.agents.security.identity_guard``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.tools import StructuredTool, tool as tool_decorator

import src.config.paths as paths_mod
from src.agents.security.identity_guard import (
    IDENTITY_FIELDS,
    IdentityMissingError,
    enforce_identity,
    filter_mcp_schema,
    wrap_tool,
    wrap_tools,
)


@dataclass
class FakeAuth:
    tenant_id: str = "moss-hub"
    user_id: str = "u_ABC"
    employee_no: str = "E0001"
    name: str = "Alice"


@pytest.fixture
def paths_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)
    yield tmp_path
    monkeypatch.setattr(paths_mod, "_paths", None)


def _make_tool():
    @tool_decorator
    def book_meeting(title: str, organizer: str) -> str:
        """Book a meeting.

        Args:
            title: Meeting title.
            organizer: Employee number of the organizer.
        """
        return f"{title} by {organizer}"

    return book_meeting


# ── enforce_identity ────────────────────────────────────────────────────


def test_enforce_overrides_identity_fields(paths_root):
    args = {"title": "t", "organizer": "E_ATTACKER"}
    out = enforce_identity(
        "book_meeting", args, FakeAuth(), declared_identity_fields=["organizer"]
    )
    assert out["organizer"] == "E0001"
    assert out["title"] == "t"


def test_enforce_injects_missing_declared_field(paths_root):
    out = enforce_identity(
        "book_meeting", {"title": "t"}, FakeAuth(), declared_identity_fields=["organizer"]
    )
    assert out["organizer"] == "E0001"


def test_enforce_fails_closed_without_auth(paths_root):
    with pytest.raises(IdentityMissingError):
        enforce_identity(
            "book_meeting",
            {"organizer": "E1"},
            None,
            declared_identity_fields=["organizer"],
        )


def test_enforce_fails_when_auth_missing_canonical_value(paths_root):
    with pytest.raises(IdentityMissingError):
        enforce_identity(
            "book_meeting",
            {"organizer": "E1"},
            FakeAuth(employee_no=""),
            declared_identity_fields=["organizer"],
        )


def test_enforce_without_identity_fields_passes_through(paths_root):
    out = enforce_identity("ping", {"x": 1}, None)
    assert out == {"x": 1}


def test_identity_fields_table_contains_expected_keys():
    for key in ("organizer", "caller", "employeeNo", "userId"):
        assert key in IDENTITY_FIELDS


# ── wrap_tool ───────────────────────────────────────────────────────────


def test_wrap_tool_preserves_name_and_description():
    t = _make_tool()
    wrapped = wrap_tool(t, FakeAuth())
    assert wrapped.name == t.name
    assert wrapped.description.splitlines()[0] == t.description.splitlines()[0]
    # The outer schema must hide identity fields from the model — the wrapper
    # injects them right before invoking the inner tool.
    assert "organizer" not in wrapped.args_schema.model_fields
    assert "title" in wrapped.args_schema.model_fields


def test_wrap_tool_enforces_on_invoke(paths_root):
    t = _make_tool()
    wrapped = wrap_tool(t, FakeAuth())
    out = wrapped.invoke({"title": "T", "organizer": "E_ATTACKER"})
    assert "E0001" in out


def test_wrap_tool_injects_missing_required_identity_field(paths_root):
    """P1 regression: a required identity field that the model omits must not
    trip Pydantic — the guard injects it from the authenticated principal."""
    t = _make_tool()  # ``organizer`` is a required positional
    wrapped = wrap_tool(t, FakeAuth())
    # Calling with only ``title`` used to raise ValidationError because the
    # original args_schema still declared ``organizer`` as required.
    out = wrapped.invoke({"title": "T"})
    assert "E0001" in out


def test_wrap_tool_mcp_schema_scenario_missing_required_identity(paths_root):
    """MCP tool that declares ``caller`` + ``organizer`` as required fields —
    after wrapping, the model can call it with just the business fields and
    the guard fills identity from the authenticated principal."""
    t = _tool_with_identity_field()
    wrapped = wrap_tool(t, FakeAuth())
    # Neither identity field supplied — guard injects both.
    out = wrapped.invoke({"title": "Sync"})
    assert out == "Sync"


def test_wrap_tools_wraps_each():
    t = _make_tool()
    wrapped = wrap_tools([t, t], FakeAuth())
    assert len(wrapped) == 2
    for w in wrapped:
        assert w.name == t.name


# ── filter_mcp_schema ───────────────────────────────────────────────────


def _tool_with_identity_field():
    @tool_decorator
    def book(title: str, organizer: str, caller: str) -> str:
        """Book something.

        Args:
            title: Title.
            organizer: Organizer.
            caller: Caller.
        """
        return title

    return book


def test_filter_removes_identity_fields_from_schema():
    t = _tool_with_identity_field()
    assert "organizer" in t.args_schema.model_fields
    assert "caller" in t.args_schema.model_fields
    filter_mcp_schema(t)
    assert "organizer" not in t.args_schema.model_fields
    assert "caller" not in t.args_schema.model_fields
    assert "title" in t.args_schema.model_fields


def test_filter_appends_description_note():
    t = _tool_with_identity_field()
    filter_mcp_schema(t)
    assert "Identity fields are injected" in t.description


def test_filter_is_noop_without_schema():
    def dummy_func(x: int) -> int:
        return x

    t = StructuredTool.from_function(dummy_func, name="dummy", description="d")
    t.args_schema = None
    assert filter_mcp_schema(t) is t
