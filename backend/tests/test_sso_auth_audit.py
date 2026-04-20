"""Tests for ``src.gateway.sso.audit.AuthAuditLedger``."""

from __future__ import annotations

import json

import pytest

import src.config.paths as paths_mod
from src.gateway.sso.audit import (
    AuthAuditLedger,
    AuthEvent,
    UNKNOWN_AUTH_AUDIT_TENANT,
)


@pytest.fixture
def paths_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)
    yield tmp_path
    monkeypatch.setattr(paths_mod, "_paths", None)


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_sso_login_written_to_user_scoped_path(paths_root):
    ledger = AuthAuditLedger()
    path = ledger.record_sso_login(
        tenant_id="moss-hub",
        user_id="u_ABC",
        employee_no="E1",
        client_ip="127.0.0.1",
    )
    assert path.exists()
    assert "moss-hub" in path.parts
    assert "u_ABC" in path.parts
    assert path.name == "auth_audit.jsonl"
    entries = _read_lines(path)
    assert entries[0]["event"] == "sso_login"
    assert entries[0]["tenant_id"] == "moss-hub"
    assert entries[0]["user_id"] == "u_ABC"
    assert entries[0]["payload"] == {"employee_no": "E1"}


def test_token_invalid_without_user_routes_to_unknown(paths_root):
    ledger = AuthAuditLedger()
    path = ledger.record_token_invalid(reason="bad signature", kid="ext")
    assert UNKNOWN_AUTH_AUDIT_TENANT in path.parts
    entries = _read_lines(path)
    assert entries[0]["event"] == "sso_token_invalid"
    assert entries[0]["reason"] == "bad signature"
    assert entries[0]["payload"] == {"kid": "ext"}


def test_forbidden_payload_keys_scrubbed(paths_root):
    ledger = AuthAuditLedger()
    path = ledger.append(
        AuthEvent(
            event="sso_login",
            tenant_id="moss-hub",
            user_id="u_ABC",
            payload={
                "ticket": "RAW",
                "jwt": "RAW",
                "app_secret": "RAW",
                "ok": "keep",
            },
        )
    )
    entries = _read_lines(path)
    assert entries[0]["payload"] == {"ok": "keep"}


def test_rejects_unknown_event_type(paths_root):
    ledger = AuthAuditLedger()
    with pytest.raises(ValueError):
        ledger.append(
            AuthEvent(event="not-a-thing", tenant_id="moss-hub", user_id="u")
        )


def test_identity_override_threshold_warning(paths_root, caplog):
    ledger = AuthAuditLedger()
    with caplog.at_level("WARNING"):
        for _ in range(ledger.IDENTITY_OVERRIDE_THRESHOLD_PER_HOUR + 1):
            ledger.record_identity_override(
                tenant_id="moss-hub",
                user_id="u_threshold",
                tool_name="book_meeting",
                field_name="organizer",
                attempted_value="E_bad",
                enforced_value="E_good",
            )
    assert any("rate exceeded threshold" in rec.message for rec in caplog.records)
