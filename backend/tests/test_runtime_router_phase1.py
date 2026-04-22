"""Phase 1 regression tests for the softened runtime contract.

Covers D1.1 (α-scheme safe defaults) and the body identity-drop invariant:

- ``POST /api/runtime/threads`` accepts omission of ``portal_session_id``
  and fills a derived default ``deerflow-web:{thread_id}``.
- ``POST /api/runtime/threads/{id}/messages:stream`` accepts omission of
  ``group_key`` (→ ``"default"``) and ``allowed_agents`` (→ tenant/user
  visible set). Explicit values still validate as before.
- ``entry_agent`` must remain inside the resolved ``allowed_agents``.
- Forged identity fields in the body (``tenant_id`` / ``user_id`` /
  ``thread_context`` / ``auth_user`` / ``configurable``) are dropped by
  pydantic ``extra="ignore"`` and do not affect the runtime ``context``
  passed to LangGraph.
- Cross-tenant / cross-user thread access still returns 403.

Tests reuse the ``_create_test_app`` scaffold from
``test_runtime_router.py`` for identity injection and agents_dir patching.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from test_runtime_router import _create_test_app


# ── Helpers ────────────────────────────────────────────────────────────


def _make_agents_dir(tmp_path: Path, names: tuple[str, ...] = ("research-agent", "data-analyst")) -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    for name in names:
        d = agents_dir / name
        d.mkdir()
        (d / "config.yaml").write_text(
            f"name: {name}\ndescription: test {name}", encoding="utf-8"
        )
    return agents_dir


def _fake_visible_agents(names: tuple[str, ...]):
    """Build a stand-in for list_all_agents(...) return value."""
    from src.config.agents_config import AgentConfig

    return [
        AgentConfig(name=n, description=f"test {n}", system_prompt_file="prompt.md")
        for n in names
    ]


# ── ThreadCreateRequest — α-scheme default ─────────────────────────────


class TestCreateThreadSoftenedContract:
    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_portal_session_id_optional_fills_default(self, mock_create, tmp_path):
        """When the client omits ``portal_session_id``, Gateway derives
        ``deerflow-web:{thread_id}`` and persists it to the registry."""
        mock_create.return_value = {"thread_id": "thread-abc"}
        app, registry, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post("/api/runtime/threads", json={})
            assert resp.status_code == 201
            data = resp.json()
            assert data["thread_id"] == "thread-abc"
            assert data["portal_session_id"] == "deerflow-web:thread-abc"
            assert registry.get_binding("thread-abc")["portal_session_id"] == "deerflow-web:thread-abc"
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_portal_session_id_explicit_still_works(self, mock_create, tmp_path):
        """External platform callers that already have a session id keep
        sending it — the default kicks in only when the field is absent."""
        mock_create.return_value = {"thread_id": "thread-abc"}
        app, registry, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads", json={"portal_session_id": "sess-123"}
            )
            assert resp.status_code == 201
            assert resp.json()["portal_session_id"] == "sess-123"
            assert registry.get_binding("thread-abc")["portal_session_id"] == "sess-123"
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_portal_session_id_whitespace_only_rejected(self, mock_create, tmp_path):
        """Whitespace-only string is still rejected with 422 — the α-scheme
        default only kicks in when the field is truly absent."""
        app, _, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads", json={"portal_session_id": "   "}
            )
            assert resp.status_code == 422
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.create_thread", new_callable=AsyncMock)
    def test_body_identity_fields_are_ignored(self, mock_create, tmp_path):
        """Forged identity fields in the create body are silently dropped by
        pydantic ``extra="ignore"`` and must not affect tenant/user on the
        registry binding (which comes from auth middleware)."""
        mock_create.return_value = {"thread_id": "thread-abc"}
        app, registry, ctx = _create_test_app(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads",
                json={
                    "tenant_id": "tenant-evil",
                    "user_id": "user-evil",
                    "thread_context": {"tenant_id": "tenant-evil"},
                    "auth_user": {"user_id": "user-evil"},
                    "configurable": {"tenant_id": "tenant-evil"},
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            # Identity comes from the injected auth middleware (default/user-1),
            # never from the body.
            assert data["tenant_id"] == "default"
            assert data["user_id"] == "user-1"
            binding = registry.get_binding("thread-abc")
            assert binding["tenant_id"] == "default"
            assert binding["user_id"] == "user-1"
        finally:
            ctx.cleanup()


# ── MessageStreamRequest — α-scheme defaults ───────────────────────────


class TestStreamMessageSoftenedContract:
    def _setup(self, tmp_path):
        agents_dir = _make_agents_dir(tmp_path)
        app, registry, ctx = _create_test_app(tmp_path, agents_dir=agents_dir)
        registry.register_binding(
            "thread-1",
            tenant_id="default",
            user_id="user-1",
            portal_session_id="sess-1",
        )
        return app, registry, ctx

    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_group_key_and_allowed_agents_optional(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """When both ``group_key`` and ``allowed_agents`` are omitted, the
        Gateway falls back to ``"default"`` and the tenant/user visible agent
        set, and still submits the run."""
        mock_start.return_value = ("chunk", None)

        async def _events(**kwargs):
            yield 'event: ack\ndata: {"thread_id": "thread-1"}\n\n'

        mock_iter.side_effect = lambda **kwargs: _events(**kwargs)
        mock_list_all.return_value = _fake_visible_agents(("research-agent", "data-analyst"))

        app, registry, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hello"},
            )
            assert resp.status_code == 200

            # Upstream submission saw Gateway-derived defaults via the
            # trusted context channel — not client-supplied values.
            kwargs = mock_start.call_args.kwargs
            sent_ctx = kwargs["context"]
            assert sent_ctx["group_key"] == "default"
            assert sorted(sent_ctx["allowed_agents"]) == ["data-analyst", "research-agent"]

            # Binding was updated with the resolved defaults.
            binding = registry.get_binding("thread-1")
            assert binding["group_key"] == "default"
            assert sorted(binding["allowed_agents"]) == ["data-analyst", "research-agent"]
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_explicit_allowed_agents_still_validated(
        self, mock_start, mock_iter, tmp_path
    ):
        """When the client passes an explicit ``allowed_agents`` list, it is
        still validated against the three-layer resolver — an unknown agent
        name is rejected with 422 regardless of the new default path."""
        app, _, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "group_key": "team-alpha",
                    "allowed_agents": ["does-not-exist"],
                },
            )
            assert resp.status_code == 422
            assert "does-not-exist" in resp.json()["detail"].lower() or "unknown" in resp.json()["detail"].lower()
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_entry_agent_must_be_in_resolved_allowed_agents(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """Even when ``allowed_agents`` is derived from the visible set, the
        ``entry_agent`` guard still fires for agents outside the set."""
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))
        app, _, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hello", "entry_agent": "data-analyst"},
            )
            assert resp.status_code == 422
            assert "allowed_agents" in resp.json()["detail"]
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.list_all_agents")
    def test_default_derivation_with_no_visible_agents_fails_422(
        self, mock_list_all, tmp_path
    ):
        """If no agents are visible to the tenant/user, the Gateway cannot
        derive a safe default — fail closed with 422, never silently submit."""
        mock_list_all.return_value = []
        app, _, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={"message": "hello"},
            )
            assert resp.status_code == 422
            assert "allowed_agents" in resp.json()["detail"]
        finally:
            ctx.cleanup()

    @patch("src.gateway.routers.runtime.list_all_agents")
    @patch("src.gateway.routers.runtime.iter_events")
    @patch("src.gateway.routers.runtime.start_stream", new_callable=AsyncMock)
    def test_body_identity_fields_do_not_affect_context(
        self, mock_start, mock_iter, mock_list_all, tmp_path
    ):
        """Forged identity fields in the stream body must not reach the
        trusted ``context`` passed upstream — identity is resolved exclusively
        from auth middleware / ``resolve_thread_context``."""
        mock_start.return_value = ("chunk", None)

        async def _events(**kwargs):
            yield 'event: ack\ndata: {"thread_id": "thread-1"}\n\n'

        mock_iter.side_effect = lambda **kwargs: _events(**kwargs)
        mock_list_all.return_value = _fake_visible_agents(("research-agent",))

        app, _, ctx = self._setup(tmp_path)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-1/messages:stream",
                json={
                    "message": "hello",
                    "tenant_id": "tenant-evil",
                    "user_id": "user-evil",
                    "thread_context": {"tenant_id": "tenant-evil"},
                    "auth_user": {"user_id": "user-evil"},
                    "configurable": {"tenant_id": "tenant-evil"},
                },
            )
            assert resp.status_code == 200

            sent_ctx = mock_start.call_args.kwargs["context"]
            assert sent_ctx["tenant_id"] == "default"
            assert sent_ctx["user_id"] == "user-1"
            # Trusted thread_context is built server-side — client input must
            # not appear in its tenant_id/user_id fields.
            assert sent_ctx["thread_context"]["tenant_id"] == "default"
            assert sent_ctx["auth_user"]["user_id"] == "user-1"
        finally:
            ctx.cleanup()

    def test_cross_tenant_thread_still_denied(self, tmp_path):
        """Thread ownership gate must still fire before the soft-default path
        runs — otherwise a browser could omit identity fields AND reach
        another tenant's thread."""
        agents_dir = _make_agents_dir(tmp_path)
        app, registry, ctx = _create_test_app(tmp_path, agents_dir=agents_dir)
        try:
            registry.register_binding(
                "thread-foreign",
                tenant_id="tenant-other",
                user_id="user-1",
                portal_session_id="sess-x",
            )
            client = TestClient(app)
            resp = client.post(
                "/api/runtime/threads/thread-foreign/messages:stream",
                json={"message": "hello"},
            )
            assert resp.status_code == 403
        finally:
            ctx.cleanup()
