"""Phase 3 / D3.1 regression tests: GATEWAY_DEBUG_LG_ERRORS gating.

Default Gateway behavior is fully sanitized: HTTP error details and
``run_failed`` SSE frames only expose stable, non-leaking text. Setting
``GATEWAY_DEBUG_LG_ERRORS`` to a truthy value enables an opt-in ``debug``
field containing the raw upstream exception class, message, and a truncated
traceback tail — intended for diagnosing LangGraph channel regressions
(``Cannot specify both configurable and context``) on live Gateways.

These tests cover:

- Default (env unset / falsy) → no ``debug`` leakage on either HTTP or SSE.
- Debug-on → ``debug`` present and contains the raw upstream exception text.
- Env-value parsing is case-insensitive and rejects unexpected strings.
- Router translation helper merges ``debug`` only when gated.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.gateway.routers.runtime import _http_exception_from_runtime_error
from src.gateway.runtime_service import (
    RuntimeServiceError,
    build_debug_error_payload,
    debug_errors_enabled,
    iter_events,
    start_stream,
)


# ── Env parsing ───────────────────────────────────────────────────────


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "True", "yes", "YES", "on", "On"])
def test_debug_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", val)
    assert debug_errors_enabled() is True


@pytest.mark.parametrize("val", ["", "0", "false", "False", "no", "off", "maybe", "  "])
def test_debug_enabled_falsy(monkeypatch, val):
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", val)
    assert debug_errors_enabled() is False


def test_debug_enabled_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_DEBUG_LG_ERRORS", raising=False)
    assert debug_errors_enabled() is False


# ── build_debug_error_payload shape ──────────────────────────────────


def test_build_debug_error_payload_shape():
    try:
        raise ValueError("Cannot specify both configurable and context")
    except ValueError as exc:
        payload = build_debug_error_payload(exc)

    assert payload["exc_type"] == "ValueError"
    assert "Cannot specify both configurable and context" in payload["exc_message"]
    assert "Traceback" in payload["traceback_tail"]
    # Bounded traceback to keep SSE frames / HTTP bodies compact.
    assert len(payload["traceback_tail"]) <= 2000


# ── RuntimeServiceError stores debug ─────────────────────────────────


def test_runtime_service_error_stores_debug_payload():
    err = RuntimeServiceError(
        "LangGraph submission failed: Runtime execution failed",
        status_code=503,
        debug={"exc_type": "ValueError", "exc_message": "boom", "traceback_tail": "..."},
    )
    assert err.status_code == 503
    assert err.debug is not None
    assert err.debug["exc_type"] == "ValueError"


def test_runtime_service_error_default_debug_is_none():
    err = RuntimeServiceError("something", status_code=404)
    assert err.debug is None


# ── Router HTTPException translation ─────────────────────────────────


def test_http_exception_default_mode_has_no_debug(monkeypatch):
    monkeypatch.delenv("GATEWAY_DEBUG_LG_ERRORS", raising=False)
    err = RuntimeServiceError(
        "LangGraph submission failed: Runtime execution failed",
        status_code=503,
        debug={"exc_type": "ValueError", "exc_message": "raw", "traceback_tail": "tb"},
    )
    http_exc = _http_exception_from_runtime_error(err)
    assert http_exc.status_code == 503
    # Default contract: detail is a plain sanitized string.
    assert isinstance(http_exc.detail, str)
    assert "raw" not in http_exc.detail
    assert "Runtime execution failed" in http_exc.detail


def test_http_exception_debug_mode_merges_debug(monkeypatch):
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", "true")
    err = RuntimeServiceError(
        "LangGraph submission failed: Runtime execution failed",
        status_code=503,
        debug={
            "exc_type": "ValueError",
            "exc_message": "Cannot specify both configurable and context",
            "traceback_tail": "Traceback ...",
        },
    )
    http_exc = _http_exception_from_runtime_error(err)
    assert http_exc.status_code == 503
    assert isinstance(http_exc.detail, dict)
    assert "error" in http_exc.detail
    assert "debug" in http_exc.detail
    assert http_exc.detail["debug"]["exc_type"] == "ValueError"
    assert "configurable" in http_exc.detail["debug"]["exc_message"]


def test_http_exception_debug_on_but_missing_debug_payload(monkeypatch):
    """If the error carries no debug payload, fall back to the sanitized form."""
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", "1")
    err = RuntimeServiceError("no-op", status_code=404, debug=None)
    http_exc = _http_exception_from_runtime_error(err)
    assert isinstance(http_exc.detail, str)


# ── iter_events run_failed gating ────────────────────────────────────


class _ExplodingIterator:
    """Async iterator whose ``__anext__`` raises, to trigger iter_events' except branch."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self._yielded = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._yielded:
            self._yielded = True
            return ("values", {})  # one noop chunk, normalized to no frames
        raise self._exc


def _collect_sse_frames(async_gen) -> list[str]:
    async def _drain():
        return [frame async for frame in async_gen]

    return asyncio.run(_drain())


def _find_run_failed_payload(frames: list[str]) -> dict | None:
    for frame in frames:
        if frame.startswith("event: run_failed"):
            # SSE format: "event: run_failed\ndata: {...}\n\n"
            data_line = [ln for ln in frame.splitlines() if ln.startswith("data: ")][0]
            return json.loads(data_line[len("data: "):])
    return None


def test_iter_events_run_failed_default_no_debug(monkeypatch):
    monkeypatch.delenv("GATEWAY_DEBUG_LG_ERRORS", raising=False)
    exc = ValueError("Cannot specify both configurable and context")
    frames = _collect_sse_frames(
        iter_events(
            thread_id="thread-xyz",
            first_chunk=None,
            upstream_iter=_ExplodingIterator(exc),
        )
    )
    payload = _find_run_failed_payload(frames)
    assert payload is not None, "run_failed SSE must be emitted on upstream exception"
    assert "debug" not in payload, (
        "Phase 3 D3.1 regression: debug payload leaked in default mode"
    )
    # Sanitized error text stays stable.
    assert payload["error"] in {
        "Runtime execution failed",
        "Upstream runtime unavailable",
        "Runtime rejected the submission",
        "Runtime thread not found",
    }


def test_iter_events_run_failed_debug_mode_includes_raw(monkeypatch):
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", "true")
    exc = ValueError("Cannot specify both configurable and context")
    frames = _collect_sse_frames(
        iter_events(
            thread_id="thread-xyz",
            first_chunk=None,
            upstream_iter=_ExplodingIterator(exc),
        )
    )
    payload = _find_run_failed_payload(frames)
    assert payload is not None
    assert "debug" in payload
    assert payload["debug"]["exc_type"] == "ValueError"
    assert "configurable" in payload["debug"]["exc_message"]
    # Sanitized error text is still present as the stable field.
    assert "error" in payload


# ── start_stream propagates debug into RuntimeServiceError ───────────


class _RejectingRuns:
    """Stand-in for ``client.runs`` whose ``stream`` yields a coroutine that
    raises immediately on first fetch — mimics LangGraph API 400."""

    def stream(self, *args, **kwargs):
        async def _aiter():
            raise ValueError("Cannot specify both configurable and context")
            yield  # pragma: no cover — makes this an async generator

        return _aiter()


# ── Non-stream HTTP path sanitization (P1 fix) ───────────────────────
#
# Regression guard: ``create_thread`` and ``get_thread_state_summary`` must
# not leak raw upstream exception text into HTTP responses when
# ``GATEWAY_DEBUG_LG_ERRORS`` is off. Before the P1 fix, both helpers
# interpolated ``{exc}`` into the ``RuntimeServiceError`` message, so
# ``_http_exception_from_runtime_error`` — which returns ``str(exc)`` in
# default mode — echoed connection strings and any secret-bearing upstream
# message back to the client.


_SECRET_UPSTREAM_TEXT = "upstream secret token abc123 dial tcp 127.0.0.1:2024 refused"


class _ExplodingThreadsClient:
    """Stand-in SDK client whose ``threads.create`` / ``threads.get_state``
    raise the same secret-bearing text we want to prove never leaks."""

    class _Threads:
        async def create(self):
            raise RuntimeError(_SECRET_UPSTREAM_TEXT)

        async def get_state(self, _thread_id):
            raise RuntimeError(_SECRET_UPSTREAM_TEXT)

    def __init__(self) -> None:
        self.threads = self._Threads()


@patch("src.gateway.runtime_service._get_client")
def test_create_thread_default_mode_does_not_leak_raw_upstream(mock_get_client, monkeypatch):
    monkeypatch.delenv("GATEWAY_DEBUG_LG_ERRORS", raising=False)
    mock_get_client.return_value = _ExplodingThreadsClient()

    from src.gateway.runtime_service import create_thread

    with pytest.raises(RuntimeServiceError) as exc_info:
        asyncio.run(create_thread())

    err = exc_info.value
    # Outer message is sanitized — no secret / connection string leakage.
    assert "abc123" not in str(err)
    assert "dial tcp" not in str(err)
    assert "127.0.0.1" not in str(err)
    # Debug still captures the raw detail server-side for opt-in exposure.
    assert err.debug is not None
    assert "abc123" in err.debug["exc_message"]

    # Simulate how the router returns this: default mode → detail is the
    # sanitized string only, with no debug leakage.
    http_exc = _http_exception_from_runtime_error(err)
    assert isinstance(http_exc.detail, str)
    assert "abc123" not in http_exc.detail
    assert "dial tcp" not in http_exc.detail


@patch("src.gateway.runtime_service._get_client")
def test_get_thread_state_summary_default_mode_does_not_leak_raw_upstream(
    mock_get_client, monkeypatch
):
    monkeypatch.delenv("GATEWAY_DEBUG_LG_ERRORS", raising=False)
    mock_get_client.return_value = _ExplodingThreadsClient()

    from src.gateway.runtime_service import get_thread_state_summary

    with pytest.raises(RuntimeServiceError) as exc_info:
        asyncio.run(get_thread_state_summary("thread-xyz"))

    err = exc_info.value
    assert "abc123" not in str(err)
    assert "dial tcp" not in str(err)
    assert err.debug is not None
    assert "abc123" in err.debug["exc_message"]

    http_exc = _http_exception_from_runtime_error(err)
    assert isinstance(http_exc.detail, str)
    assert "abc123" not in http_exc.detail
    assert "dial tcp" not in http_exc.detail


@patch("src.gateway.runtime_service._get_client")
def test_create_thread_debug_mode_exposes_raw_upstream(mock_get_client, monkeypatch):
    """Opt-in path: debug-on must surface the raw text so operators can diagnose."""
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", "true")
    mock_get_client.return_value = _ExplodingThreadsClient()

    from src.gateway.runtime_service import create_thread

    with pytest.raises(RuntimeServiceError) as exc_info:
        asyncio.run(create_thread())

    http_exc = _http_exception_from_runtime_error(exc_info.value)
    assert isinstance(http_exc.detail, dict)
    assert "abc123" in http_exc.detail["debug"]["exc_message"]
    # And the stable sanitized text is still present for platform consumers.
    assert "abc123" not in http_exc.detail["error"]


@patch("src.gateway.runtime_service._get_client")
def test_get_thread_state_summary_debug_mode_exposes_raw_upstream(mock_get_client, monkeypatch):
    monkeypatch.setenv("GATEWAY_DEBUG_LG_ERRORS", "1")
    mock_get_client.return_value = _ExplodingThreadsClient()

    from src.gateway.runtime_service import get_thread_state_summary

    with pytest.raises(RuntimeServiceError) as exc_info:
        asyncio.run(get_thread_state_summary("thread-xyz"))

    http_exc = _http_exception_from_runtime_error(exc_info.value)
    assert isinstance(http_exc.detail, dict)
    assert "abc123" in http_exc.detail["debug"]["exc_message"]
    assert "abc123" not in http_exc.detail["error"]


@patch("src.gateway.runtime_service._get_client")
def test_start_stream_captures_debug_on_upstream_rejection(mock_get_client):
    client = MagicMock()
    client.runs = _RejectingRuns()
    mock_get_client.return_value = client

    ctx = {
        "thread_id": "t",
        "tenant_id": "tn",
        "user_id": "u",
        "thread_context": {},
        "auth_user": {},
    }

    with pytest.raises(RuntimeServiceError) as exc_info:
        asyncio.run(start_stream(thread_id="t", message="hi", context=ctx))

    err = exc_info.value
    # Debug is always captured — visibility is gated at the router/SSE layer.
    assert err.debug is not None
    assert err.debug["exc_type"] == "ValueError"
    assert "configurable" in err.debug["exc_message"]
    # Sanitized message stays stable.
    assert "LangGraph submission failed" in str(err)
