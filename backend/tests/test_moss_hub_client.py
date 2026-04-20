"""Tests for ``src.gateway.sso.moss_hub_client.verify_ticket``."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from src.gateway.sso.config import SSOConfig
from src.gateway.sso.models import SsoTicketInvalid, SsoUpstreamError
from src.gateway.sso import moss_hub_client


def _cfg() -> SSOConfig:
    return SSOConfig(
        enabled=True,
        moss_hub_base_url="https://moss-hub.example",
        moss_hub_app_key="app-key-1",
        moss_hub_app_secret="s" * 40,
        jwt_secret="j" * 40,
    )


def _install_transport(monkeypatch, handler):
    """Install a MockTransport-backed AsyncClient into httpx.AsyncClient."""
    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def _patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched)


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode("utf-8"),
                          headers={"content-type": "application/json"})


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_success(monkeypatch):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return _json_response({
            "code": "0000",
            "message": "ok",
            "data": {
                "userId": "10086",
                "employeeNo": "E0001",
                "name": "Alice",
                "targetSystem": "luliu",
            },
        })

    _install_transport(monkeypatch, handler)
    profile = asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))
    assert profile.raw_user_id == "10086"
    assert profile.employee_no == "E0001"
    assert profile.name == "Alice"
    assert profile.target_system == "luliu"
    assert captured["body"] == {"ticket": "tkt"}
    assert captured["headers"]["x-app-key"] == "app-key-1"
    assert "x-sign" in captured["headers"]
    assert "x-timestamp" in captured["headers"]
    assert "x-nonce" in captured["headers"]
    assert captured["url"].endswith("/api/open/sso/luliu/verify-ticket")


@pytest.mark.parametrize("code", ["B002", "B003", "B004"])
def test_invalid_ticket_codes_raise_401_mapped(monkeypatch, code):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": code, "message": "bad", "data": None})

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoTicketInvalid):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_unknown_code_is_upstream_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "X999", "message": "boom"})

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoUpstreamError):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_timeout_is_upstream_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoUpstreamError, match="timed out"):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_network_error_is_upstream_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoUpstreamError):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_5xx_is_upstream_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"nope")

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoUpstreamError, match="503"):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_unexpected_target_system(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({
            "code": "0000",
            "data": {
                "userId": "u", "employeeNo": "E", "name": "N",
                "targetSystem": "other",
            },
        })

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoUpstreamError, match="targetSystem"):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_missing_fields(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({
            "code": "0000",
            "data": {"userId": "u", "name": "N", "targetSystem": "luliu"},
        })

    _install_transport(monkeypatch, handler)
    with pytest.raises(SsoUpstreamError, match="missing"):
        asyncio.run(moss_hub_client.verify_ticket("tkt", config=_cfg()))


def test_empty_ticket_rejected_without_network():
    # Should fail fast without calling moss-hub (no transport installed).
    with pytest.raises(SsoTicketInvalid):
        asyncio.run(moss_hub_client.verify_ticket("   ", config=_cfg()))
