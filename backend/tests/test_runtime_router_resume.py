"""Phase 2.1 regression tests for the Gateway resume endpoint.

Covers D2.1 acceptance:

- ``POST /api/runtime/threads/{id}/resume`` authenticates via auth middleware,
  enforces thread ownership (403 on cross-tenant/user), forbids browser
  identity smuggling, forwards checkpoint/workflow-resume hints into the
  trusted ``context``, and submits to LangGraph with a single ``context``
  channel (LG1.x dual-channel is rejected).
- ``goto`` is gated by a Gateway-owned whitelist. Unlisted values → 422.
- ``message`` and ``command`` may both be omitted individually, but at least
  one resume driver (message / interrupt_feedback / goto) must be present.
- Checkpoint is opaque but identity keys inside it are stripped before
  forwarding.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from test_runtime_router import _create_test_app


def _setup(tmp_path: Path, **kwargs):
    app, registry, ctx = _create_test_app(tmp_path, **kwargs)
    registry.register_binding(
        "thread-1",
        tenant_id="default",
        user_id="user-1",
        portal_session_id="sess-1",
        group_key="team-alpha",
        allowed_agents=["research-agent"],
        entry_agent="research-agent",
        requested_orchestration_mode="workflow",
    )
    return app, registry, ctx


def _fake_events_factory():
    async def fake_events(**kwargs):
        yield 'event: ack\ndata: {"thread_id": "thread-1"}\n\n'
        yield 'event: run_completed\ndata: {"thread_id": "thread-1"}\n\n'

    return fake_events


class TestResumeEndpointSuccess:
    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_with_message_and_checkpoint(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            checkpoint = {"checkpoint_id": "ckpt-42", "thread_ts": "2026-04-22T00:00:00Z"}
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "message": "[intervention_resolved] request_id=req-1 action_key=confirm",
                    "checkpoint": checkpoint,
                    "workflow_clarification_resume": True,
                    "workflow_resume_run_id": "run-99",
                    "workflow_resume_task_id": "task-7",
                },
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            # Verify identity + workflow hints reach the service layer
            kwargs = mock_start.call_args.kwargs
            ctx_sent = kwargs["context"]
            assert ctx_sent["thread_id"] == "thread-1"
            assert ctx_sent["tenant_id"] == "default"
            assert ctx_sent["user_id"] == "user-1"
            assert ctx_sent["thread_context"]["thread_id"] == "thread-1"
            assert ctx_sent["auth_user"]["user_id"] == "user-1"
            assert ctx_sent["workflow_clarification_resume"] is True
            assert ctx_sent["workflow_resume_run_id"] == "run-99"
            assert ctx_sent["workflow_resume_task_id"] == "task-7"
            # Registry-bound routing is inherited, not widened by the browser
            assert ctx_sent["group_key"] == "team-alpha"
            assert ctx_sent["allowed_agents"] == ["research-agent"]
            assert ctx_sent["agent_name"] == "research-agent"
            assert ctx_sent["requested_orchestration_mode"] == "workflow"

            assert kwargs["message"] == (
                "[intervention_resolved] request_id=req-1 action_key=confirm"
            )
            assert kwargs["checkpoint"] == checkpoint
            assert kwargs["command"] is None
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_with_command_only(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "interrupt_feedback": {"answer": "approve"},
                },
            )
            assert resp.status_code == 200

            kwargs = mock_start.call_args.kwargs
            assert kwargs["message"] is None
            assert kwargs["command"] == {"resume": {"answer": "approve"}}
        finally:
            ctx.cleanup()


class TestResumeEndpointSecurity:
    def test_resume_cross_tenant_denied(self, tmp_path):
        app, registry, ctx = _create_test_app(tmp_path)
        try:
            registry.register_binding(
                "thread-other",
                tenant_id="tenant-other",
                user_id="user-1",
                portal_session_id="sess-9",
            )
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-other/resume",
                json={"message": "resume"},
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    def test_resume_thread_not_found(self, tmp_path):
        app, _, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/nonexistent/resume",
                json={"message": "resume"},
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_drops_body_identity(self, mock_iter, mock_start, tmp_path):
        """Browser-supplied identity fields must be silently dropped."""
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "message": "hi",
                    "tenant_id": "forged-tenant",
                    "user_id": "forged-user",
                    "thread_context": {"thread_id": "forged"},
                    "auth_user": {"user_id": "forged"},
                    "config": {"configurable": {"thread_context": {"x": 1}}},
                    "configurable": {"thread_context": {"x": 1}},
                },
            )
            assert resp.status_code == 200

            ctx_sent = mock_start.call_args.kwargs["context"]
            assert ctx_sent["tenant_id"] == "default"
            assert ctx_sent["user_id"] == "user-1"
            assert ctx_sent["thread_context"]["thread_id"] == "thread-1"
            assert ctx_sent["auth_user"]["user_id"] == "user-1"
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_strips_identity_from_checkpoint(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "message": "hi",
                    "checkpoint": {
                        "checkpoint_id": "ckpt-1",
                        "tenant_id": "forged",
                        "thread_context": {"thread_id": "forged"},
                    },
                },
            )
            assert resp.status_code == 200

            checkpoint = mock_start.call_args.kwargs["checkpoint"]
            assert checkpoint["checkpoint_id"] == "ckpt-1"
            assert "tenant_id" not in checkpoint
            assert "thread_context" not in checkpoint
        finally:
            ctx.cleanup()

    def test_resume_rejects_unknown_goto(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={"goto": "__internal_node__"},
            )
            assert resp.status_code == 422
            assert "goto" in resp.json()["detail"]
        finally:
            ctx.cleanup()

    def test_resume_rejects_empty_body(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post("/api/runtime/threads/thread-1/resume", json={})
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_resume_rejects_whitespace_message(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={"message": "   "},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()


class TestResumeAppContext:
    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_forwards_app_context_fields(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "message": "resume",
                    "app_context": {
                        "thinking_enabled": True,
                        "is_plan_mode": True,
                        "subagent_enabled": False,
                    },
                },
            )
            assert resp.status_code == 200

            ctx_sent = mock_start.call_args.kwargs["context"]
            assert ctx_sent["thinking_enabled"] is True
            assert ctx_sent["is_plan_mode"] is True
            assert ctx_sent["subagent_enabled"] is False
        finally:
            ctx.cleanup()

    def test_resume_rejects_forbidden_app_context_key(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "message": "resume",
                    "app_context": {"tenant_id": "forged"},
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_resume_rejects_oversized_message(self, tmp_path):
        from src.gateway.routers.runtime import MAX_RESUME_MESSAGE_LENGTH

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={"message": "x" * (MAX_RESUME_MESSAGE_LENGTH + 1)},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()


class TestResumeNestedPayload:
    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_accepts_nested_resume_payload_message(self, mock_iter, mock_start, tmp_path):
        """Phase 2.1 spec contract: ``resume_payload.message`` is accepted."""
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            checkpoint = {"checkpoint_id": "ckpt-5"}
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "resume_payload": {"message": "nested resume text"},
                    "checkpoint": checkpoint,
                    "workflow_clarification_resume": True,
                    "workflow_resume_run_id": "run-7",
                    "workflow_resume_task_id": "task-3",
                },
            )
            assert resp.status_code == 200

            kwargs = mock_start.call_args.kwargs
            assert kwargs["message"] == "nested resume text"
            assert kwargs["checkpoint"] == checkpoint
            ctx_sent = kwargs["context"]
            assert ctx_sent["workflow_clarification_resume"] is True
            assert ctx_sent["workflow_resume_run_id"] == "run-7"
            assert ctx_sent["workflow_resume_task_id"] == "task-3"
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_resume_top_level_message_wins_over_nested(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={
                    "message": "top-level",
                    "resume_payload": {"message": "nested"},
                },
            )
            assert resp.status_code == 200
            assert mock_start.call_args.kwargs["message"] == "top-level"
        finally:
            ctx.cleanup()

    def test_resume_rejects_unknown_resume_payload_key(self, tmp_path):
        """``ResumePayload`` is ``extra="forbid"``; unknown keys → 422."""
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={"resume_payload": {"message": "hi", "tenant_id": "forged"}},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()


class TestResumeUpstreamErrors:
    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    def test_resume_upstream_404(self, mock_start, tmp_path):
        from src.gateway.runtime_service import RuntimeServiceError

        mock_start.side_effect = RuntimeServiceError("thread missing upstream", status_code=404)
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={"message": "resume"},
            )
            assert resp.status_code == 404
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_resume_stream", new_callable=AsyncMock)
    def test_resume_upstream_409(self, mock_start, tmp_path):
        from src.gateway.runtime_service import RuntimeServiceError

        mock_start.side_effect = RuntimeServiceError("already running", status_code=409)
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/resume",
                json={"message": "resume"},
            )
            assert resp.status_code == 409
        finally:
            ctx.cleanup()
