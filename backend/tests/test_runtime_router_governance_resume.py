"""Phase 2.2 regression tests for the Gateway governance:resume endpoint.

Covers D2.2 acceptance:

- ``POST /api/runtime/threads/{id}/governance:resume`` authenticates via the
  auth middleware, enforces thread ownership (403 on cross-tenant/user),
  forbids browser identity smuggling, forwards workflow_resume / governance
  hints into the trusted ``context``, and submits to LangGraph with a single
  ``context`` channel (LG1.x dual-channel rejected).
- ``message`` is required and non-empty; ``governance_id`` is required.
- ``workflow_clarification_resume`` is forced to True server-side — the
  browser cannot flip it off.
- ``app_context`` is forwarded but ``extra="forbid"`` keeps identity
  smuggling visible as HTTP 422.
- Upstream SDK rejections (404 / 409) surface as matching HTTP status.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from test_runtime_router import _create_test_app


def _patch_ledger(
    *,
    thread_id: str = "thread-1",
    tenant_id: str = "default",
    user_id: str = "user-1",
    run_id: str | None = "run-ledger",
    task_id: str | None = "task-ledger",
    status: str = "pending_intervention",
):
    """Patch ``governance_ledger.get_by_id`` to return a well-formed entry.

    Every governance resume path now validates the ledger before trusting
    ``governance_id`` / ``workflow_resume_run_id`` / ``workflow_resume_task_id``
    from the browser. Tests that exercise the happy path must stage an entry
    that matches the request's thread / tenant / user.
    """
    def _get(gid: str):
        return {
            "governance_id": gid,
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": task_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "status": status,
            "source_agent": "test-agent",
            "hook_name": "before_interrupt_emit",
            "source_path": "test",
            "risk_level": "low",
            "category": "test",
            "decision": "require_intervention",
            "created_at": "2026-01-01T00:00:00Z",
        }

    return patch(
        "src.gateway.routers.runtime.governance_ledger.get_by_id",
        side_effect=_get,
    )


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


class TestGovernanceResumeSuccess:
    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_governance_resume_forwards_trusted_fields(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(run_id="run-9", task_id="task-3"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={
                        "message": "approved — continue",
                        "governance_id": "gov-42",
                        "workflow_resume_run_id": "run-9",
                        "workflow_resume_task_id": "task-3",
                    },
                )
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")

                kwargs = mock_start.call_args.kwargs
                assert kwargs["message"] == "approved — continue"
                # Governance resume never sends a checkpoint or Command — the
                # dedicated service fn signature does not accept either.
                assert "checkpoint" not in kwargs
                assert "command" not in kwargs
                # Binding is workflow → subgraph streaming stays off to keep the
                # outer workflow transcript clean.
                assert kwargs["stream_subgraphs"] is False

                ctx_sent = kwargs["context"]
                assert ctx_sent["thread_id"] == "thread-1"
                assert ctx_sent["tenant_id"] == "default"
                assert ctx_sent["user_id"] == "user-1"
                assert ctx_sent["thread_context"]["thread_id"] == "thread-1"
                assert ctx_sent["auth_user"]["user_id"] == "user-1"
                # Governance marker is server-enforced.
                assert ctx_sent["workflow_clarification_resume"] is True
                # Ledger is authoritative for workflow_resume_* — body values
                # only pass validation when they match.
                assert ctx_sent["workflow_resume_run_id"] == "run-9"
                assert ctx_sent["workflow_resume_task_id"] == "task-3"
                assert ctx_sent["governance_id"] == "gov-42"
                # Registry-bound routing inherited from the original run.
                assert ctx_sent["group_key"] == "team-alpha"
                assert ctx_sent["allowed_agents"] == ["research-agent"]
                assert ctx_sent["agent_name"] == "research-agent"
                assert ctx_sent["requested_orchestration_mode"] == "workflow"
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_governance_resume_forwards_app_context(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger():
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={
                        "message": "resume",
                        "governance_id": "gov-1",
                        "app_context": {
                            "thinking_enabled": True,
                            "is_plan_mode": False,
                            "subagent_enabled": True,
                        },
                    },
                )
                assert resp.status_code == 200

                ctx_sent = mock_start.call_args.kwargs["context"]
                assert ctx_sent["thinking_enabled"] is True
                assert ctx_sent["is_plan_mode"] is False
                assert ctx_sent["subagent_enabled"] is True
        finally:
            ctx.cleanup()


class TestGovernanceResumeStreamSubgraphs:
    """Stream-subgraph toggle mirrors ``buildGovernanceResumeRequest``:

    ``streamSubgraphs: settings.requested_orchestration_mode !== "workflow"``

    The toggle is derived server-side from the bound mode — the browser
    cannot flip it. workflow → False; leader / auto / unbound → True.
    """

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_stream_subgraphs_true_for_leader_mode(self, mock_iter, mock_start, tmp_path):
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        from test_runtime_router import _create_test_app

        app, registry, ctx = _create_test_app(tmp_path)
        try:
            registry.register_binding(
                "thread-leader",
                tenant_id="default",
                user_id="user-1",
                portal_session_id="sess-leader",
                group_key="team-alpha",
                allowed_agents=["research-agent"],
                entry_agent="research-agent",
                requested_orchestration_mode="leader",
            )
            with _patch_ledger(thread_id="thread-leader"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-leader/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 200
                assert mock_start.call_args.kwargs["stream_subgraphs"] is True
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_stream_subgraphs_true_when_mode_unbound(self, mock_iter, mock_start, tmp_path):
        """Unbound / missing mode defaults to True (legacy parity — non-workflow)."""
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        from test_runtime_router import _create_test_app

        app, registry, ctx = _create_test_app(tmp_path)
        try:
            registry.register_binding(
                "thread-nomode",
                tenant_id="default",
                user_id="user-1",
                portal_session_id="sess-nomode",
            )
            with _patch_ledger(thread_id="thread-nomode"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-nomode/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 200
                assert mock_start.call_args.kwargs["stream_subgraphs"] is True
        finally:
            ctx.cleanup()


class TestGovernanceResumeSecurity:
    def test_governance_resume_cross_tenant_denied(self, tmp_path):
        from test_runtime_router import _create_test_app as _make_app

        app, registry, ctx = _make_app(tmp_path)
        try:
            registry.register_binding(
                "thread-other",
                tenant_id="tenant-other",
                user_id="user-1",
                portal_session_id="sess-9",
            )
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-other/governance:resume",
                json={"message": "resume", "governance_id": "gov-1"},
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    def test_governance_resume_thread_not_found(self, tmp_path):
        from test_runtime_router import _create_test_app as _make_app

        app, _, ctx = _make_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/nonexistent/governance:resume",
                json={"message": "resume", "governance_id": "gov-1"},
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_governance_resume_drops_body_identity(self, mock_iter, mock_start, tmp_path):
        """Browser-supplied identity fields must be silently dropped."""
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger():
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={
                        "message": "resume",
                        "governance_id": "gov-1",
                        "tenant_id": "forged-tenant",
                        "user_id": "forged-user",
                        "thread_context": {"thread_id": "forged"},
                        "auth_user": {"user_id": "forged"},
                        "config": {"configurable": {"thread_context": {"x": 1}}},
                        "configurable": {"thread_context": {"x": 1}},
                        "workflow_clarification_resume": False,  # ignored; server forces True
                    },
                )
                assert resp.status_code == 200

                ctx_sent = mock_start.call_args.kwargs["context"]
                assert ctx_sent["tenant_id"] == "default"
                assert ctx_sent["user_id"] == "user-1"
                assert ctx_sent["thread_context"]["thread_id"] == "thread-1"
                assert ctx_sent["auth_user"]["user_id"] == "user-1"
                # Server-enforced marker survives a browser attempt to flip it.
                assert ctx_sent["workflow_clarification_resume"] is True
        finally:
            ctx.cleanup()

    def test_governance_resume_rejects_forbidden_app_context_key(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/governance:resume",
                json={
                    "message": "resume",
                    "governance_id": "gov-1",
                    "app_context": {"tenant_id": "forged"},
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_governance_resume_rejects_missing_message(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/governance:resume",
                json={"governance_id": "gov-1"},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_governance_resume_rejects_whitespace_message(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/governance:resume",
                json={"message": "   ", "governance_id": "gov-1"},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_governance_resume_rejects_missing_governance_id(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/governance:resume",
                json={"message": "resume"},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_governance_resume_rejects_whitespace_governance_id(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/governance:resume",
                json={"message": "resume", "governance_id": "   "},
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_governance_resume_rejects_oversized_message(self, tmp_path):
        from src.gateway.routers.runtime import MAX_RESUME_MESSAGE_LENGTH

        app, _, ctx = _setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/governance:resume",
                json={
                    "message": "x" * (MAX_RESUME_MESSAGE_LENGTH + 1),
                    "governance_id": "gov-1",
                },
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()


class TestGovernanceResumeUpstreamErrors:
    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    def test_governance_resume_upstream_404(self, mock_start, tmp_path):
        from src.gateway.runtime_service import RuntimeServiceError

        mock_start.side_effect = RuntimeServiceError("thread missing upstream", status_code=404)
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger():
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 404
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    def test_governance_resume_upstream_409(self, mock_start, tmp_path):
        from src.gateway.runtime_service import RuntimeServiceError

        mock_start.side_effect = RuntimeServiceError("already running", status_code=409)
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger():
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 409
        finally:
            ctx.cleanup()


class TestGovernanceResumeLedgerGuard:
    """Ledger is authoritative for governance correlation.

    The Gateway must refuse to resume when the browser-supplied
    ``governance_id`` is unknown, belongs to a different tenant / user /
    thread, is already resolved, or carries mismatched
    ``workflow_resume_run_id`` / ``workflow_resume_task_id`` hints.
    """

    def test_rejects_unknown_governance_id(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with patch(
                "src.gateway.routers.runtime.governance_ledger.get_by_id",
                return_value=None,
            ):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-unknown"},
                )
                assert resp.status_code == 404
        finally:
            ctx.cleanup()

    def test_rejects_ledger_entry_for_different_thread(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(thread_id="thread-other"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 403
        finally:
            ctx.cleanup()

    def test_rejects_ledger_entry_for_different_tenant(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(tenant_id="tenant-other"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 403
        finally:
            ctx.cleanup()

    def test_rejects_ledger_entry_for_different_user(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(user_id="user-other"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 403
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_allows_resolved_entry(self, mock_iter, mock_start, tmp_path):
        """Real flow: ``operator_resolve`` flips ledger → ``resolved`` *before*
        the browser calls ``governance:resume``. The endpoint must accept that
        normal sequence, not only the pre-resolve racy state."""
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(status="resolved"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 200
        finally:
            ctx.cleanup()

    def test_rejects_rejected_entry(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(status="rejected"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 409
        finally:
            ctx.cleanup()

    def test_rejects_failed_entry(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(status="failed"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 409
        finally:
            ctx.cleanup()

    def test_rejects_expired_entry(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(status="expired"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 409
        finally:
            ctx.cleanup()

    def test_rejects_decided_entry(self, tmp_path):
        """``decided`` is immediate allow/deny — there is no human interrupt
        to resume, so governance:resume for such entries must fail-closed."""
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(status="decided"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 409
        finally:
            ctx.cleanup()

    def test_rejects_mismatched_run_id(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(run_id="run-real"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={
                        "message": "resume",
                        "governance_id": "gov-1",
                        "workflow_resume_run_id": "run-forged",
                    },
                )
                assert resp.status_code == 422
        finally:
            ctx.cleanup()

    def test_rejects_mismatched_task_id(self, tmp_path):
        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(task_id="task-real"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={
                        "message": "resume",
                        "governance_id": "gov-1",
                        "workflow_resume_task_id": "task-forged",
                    },
                )
                assert resp.status_code == 422
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.start_governance_resume_stream", new_callable=AsyncMock)
    @patch("src.gateway.routers.runtime.iter_events")
    def test_ledger_is_authoritative_for_workflow_resume_ids(
        self, mock_iter, mock_start, tmp_path
    ):
        """When the body omits hints, ledger-sourced run_id/task_id flow into context."""
        mock_start.return_value = ("fake_chunk", None)
        mock_iter.side_effect = lambda **_: _fake_events_factory()()

        app, _, ctx = _setup(tmp_path)
        try:
            with _patch_ledger(run_id="run-from-ledger", task_id="task-from-ledger"):
                client = TestClient(app)
                resp = client.post(
                    "/api/runtime/threads/thread-1/governance:resume",
                    json={"message": "resume", "governance_id": "gov-1"},
                )
                assert resp.status_code == 200

                ctx_sent = mock_start.call_args.kwargs["context"]
                assert ctx_sent["workflow_resume_run_id"] == "run-from-ledger"
                assert ctx_sent["workflow_resume_task_id"] == "task-from-ledger"
        finally:
            ctx.cleanup()
