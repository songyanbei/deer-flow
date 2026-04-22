"""Phase 1 D1.2 — app_context contract tests for runtime stream endpoint.

Covers the 4 acceptance items listed in
``collaboration/handoffs/frontend-to-backend.md`` §"Gateway runtime context
does not forward app-level submit fields":

1. Each of the 8 app-level fields round-trips from the submit body into the
   LangGraph runtime ``context`` dict when supplied, and is absent when not
   supplied (no default leakage).
2. Supplying an unknown key under ``app_context`` returns HTTP 422
   (prevents silent drops — the regression that motivated this work).
3. Supplying identity fields (``tenant_id`` / ``user_id`` / ``thread_id`` /
   ``thread_context`` / ``auth_user``) inside ``app_context`` is rejected
   at the schema layer (extra="forbid"); even if they somehow landed in
   ``app_fields``, the router's identity-merge-last step overrides them.
4. ``app_context`` is NOT persisted to ThreadRegistry (per-run state only).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from test_runtime_router import _create_test_app
from test_runtime_router_phase1 import _fake_visible_agents, _make_agents_dir


class _StreamFixture:
    """Bundle the scaffolding every app_context stream test needs."""

    def __init__(self, tmp_path: Path):
        agents_dir = _make_agents_dir(tmp_path)
        self.app, self.registry, self.ctx = _create_test_app(tmp_path, agents_dir=agents_dir)
        self.registry.register_binding(
            "thread-1",
            tenant_id="default",
            user_id="user-1",
            portal_session_id="sess-1",
        )

    def close(self) -> None:
        self.ctx.cleanup()


def _dummy_events_side_effect():
    async def _events(**kwargs):
        yield 'event: ack\ndata: {"thread_id": "thread-1"}\n\n'

    return lambda **kwargs: _events(**kwargs)


# ── 1. Round-trip — all 8 fields forwarded into context ───────────────


class TestAppContextRoundTrip:
    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_all_eight_fields_round_trip(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        mock_start.return_value = ("chunk", None)
        mock_iter.side_effect = _dummy_events_side_effect()
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))

        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "app_context": {
                        "thinking_enabled": True,
                        "is_plan_mode": True,
                        "subagent_enabled": True,
                        "is_bootstrap": False,
                        "workflow_clarification_resume": True,
                        "workflow_resume_run_id": "run-abc",
                        "workflow_resume_task_id": "task-xyz",
                        "workflow_clarification_response": {
                            "answers": {
                                "q1": {"text": "yes"},
                                "q2": {"text": "no"},
                            }
                        },
                    },
                },
            )
            assert resp.status_code == 200

            sent_ctx = mock_start.call_args.kwargs["context"]
            assert sent_ctx["thinking_enabled"] is True
            assert sent_ctx["is_plan_mode"] is True
            assert sent_ctx["subagent_enabled"] is True
            assert sent_ctx["is_bootstrap"] is False
            assert sent_ctx["workflow_clarification_resume"] is True
            assert sent_ctx["workflow_resume_run_id"] == "run-abc"
            assert sent_ctx["workflow_resume_task_id"] == "task-xyz"
            assert sent_ctx["workflow_clarification_response"] == {
                "answers": {
                    "q1": {"text": "yes"},
                    "q2": {"text": "no"},
                }
            }
        finally:
            fx.close()

    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_absent_app_context_yields_no_app_keys(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """Absent ``app_context`` → none of the 8 keys appear in context."""
        mock_start.return_value = ("chunk", None)
        mock_iter.side_effect = _dummy_events_side_effect()
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))

        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hello"},
            )
            assert resp.status_code == 200

            sent_ctx = mock_start.call_args.kwargs["context"]
            for key in (
                "thinking_enabled",
                "is_plan_mode",
                "subagent_enabled",
                "is_bootstrap",
                "workflow_clarification_resume",
                "workflow_resume_run_id",
                "workflow_resume_task_id",
                "workflow_clarification_response",
            ):
                assert key not in sent_ctx, f"{key} leaked into context when app_context was absent"
        finally:
            fx.close()

    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_partial_app_context_only_forwards_supplied_fields(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """``exclude_none=True`` means fields the client did not set stay
        absent — no ``None`` leakage that would overwrite agent defaults."""
        mock_start.return_value = ("chunk", None)
        mock_iter.side_effect = _dummy_events_side_effect()
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))

        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "app_context": {"thinking_enabled": True},
                },
            )
            assert resp.status_code == 200

            sent_ctx = mock_start.call_args.kwargs["context"]
            assert sent_ctx["thinking_enabled"] is True
            for key in (
                "is_plan_mode",
                "subagent_enabled",
                "is_bootstrap",
                "workflow_clarification_resume",
                "workflow_resume_run_id",
                "workflow_resume_task_id",
                "workflow_clarification_response",
            ):
                assert key not in sent_ctx
        finally:
            fx.close()


# ── 2. Unknown keys → 422 (the motivating regression) ─────────────────


class TestAppContextExtraForbid:
    def test_unknown_top_level_key_returns_422(self, tmp_path):
        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "app_context": {"mystery_knob": "oops"},
                },
            )
            assert resp.status_code == 422
            body = resp.json()
            # Pydantic v2 extra=forbid surfaces the offending key in detail
            assert "mystery_knob" in str(body).lower() or "extra" in str(body).lower()
        finally:
            fx.close()

    def test_unknown_key_in_clarification_response_returns_422(self, tmp_path):
        """Nested ``WorkflowClarificationResponse`` also has extra=forbid."""
        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "app_context": {
                        "workflow_clarification_response": {
                            "answers": {"q1": {"text": "ok"}},
                            "unexpected": "field",
                        }
                    },
                },
            )
            assert resp.status_code == 422
        finally:
            fx.close()

    def test_unknown_key_in_clarification_answer_returns_422(self, tmp_path):
        """``ClarificationAnswer`` also has extra=forbid."""
        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "app_context": {
                        "workflow_clarification_response": {
                            "answers": {"q1": {"text": "ok", "priority": 1}}
                        }
                    },
                },
            )
            assert resp.status_code == 422
        finally:
            fx.close()


# ── 3. Identity defense — identity fields inside app_context are rejected ─


class TestAppContextIdentityDefense:
    def test_identity_fields_inside_app_context_are_rejected(self, tmp_path):
        """extra=forbid on AppRuntimeContext means smuggling identity
        fields under app_context fails the schema (422) — no silent merge."""
        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            for identity_key in (
                "tenant_id",
                "user_id",
                "thread_id",
                "thread_context",
                "auth_user",
            ):
                resp = client.post(
                    "/api/runtime/threads/thread-1/messages:stream",
                    json={
                        "message": "hello",
                        "app_context": {identity_key: "spoofed"},
                    },
                )
                assert resp.status_code == 422, f"identity key {identity_key} was not rejected"
        finally:
            fx.close()

    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_top_level_body_identity_fields_still_dropped(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """Top-level body identity fields continue to be silently dropped
        by MessageStreamRequest.extra='ignore' (existing invariant). They
        must not affect the trusted ``context`` even when combined with a
        valid app_context."""
        mock_start.return_value = ("chunk", None)
        mock_iter.side_effect = _dummy_events_side_effect()
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))

        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "tenant_id": "tenant-evil",
                    "user_id": "user-evil",
                    "app_context": {"thinking_enabled": True},
                },
            )
            assert resp.status_code == 200

            sent_ctx = mock_start.call_args.kwargs["context"]
            assert sent_ctx["tenant_id"] == "default"
            assert sent_ctx["user_id"] == "user-1"
            assert sent_ctx["thinking_enabled"] is True
        finally:
            fx.close()


# ── 4. app_context is per-run — not persisted to ThreadRegistry ───────


class TestAppContextNotPersisted:
    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_app_context_fields_not_in_binding(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """The registry stores only group_key / allowed_agents / entry_agent /
        requested_orchestration_mode / metadata. No app_context field should
        leak into the binding dict."""
        mock_start.return_value = ("chunk", None)
        mock_iter.side_effect = _dummy_events_side_effect()
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))

        fx = _StreamFixture(tmp_path)
        try:
            client = TestClient(fx.app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "app_context": {
                        "thinking_enabled": True,
                        "workflow_resume_run_id": "run-abc",
                    },
                },
            )
            assert resp.status_code == 200

            binding = fx.registry.get_binding("thread-1")
            for key in (
                "thinking_enabled",
                "is_plan_mode",
                "subagent_enabled",
                "is_bootstrap",
                "workflow_clarification_resume",
                "workflow_resume_run_id",
                "workflow_resume_task_id",
                "workflow_clarification_response",
                "app_context",
            ):
                assert key not in binding, f"{key} leaked into ThreadRegistry binding"
        finally:
            fx.close()
