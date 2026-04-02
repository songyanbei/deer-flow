"""Multi-tenant completion tests.

Tests for the governance tenant access control and user-profile tenant isolation
fixes described in multi-tenant-completion.md.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.routers import agents, governance


# ── Test App Setup ───────────────────────────────────────────────────────


def _make_app(*, tenant_id: str = "default") -> FastAPI:
    """Create a minimal FastAPI app with governance + agents routers.

    Overrides get_tenant_id to return the given tenant_id.
    """
    app = FastAPI()
    app.include_router(governance.router)
    app.include_router(agents.router)

    from src.gateway.dependencies import get_tenant_id

    app.dependency_overrides[get_tenant_id] = lambda: tenant_id
    return app


# ── Governance Tenant Isolation ──────────────────────────────────────────


class TestGovernanceDetailTenantCheck:
    """GET /api/governance/{id} must enforce tenant ownership."""

    def test_own_tenant_can_access(self):
        app = _make_app(tenant_id="tenant-a")
        client = TestClient(app)

        # Seed a governance entry for tenant-a
        from src.agents.governance.ledger import governance_ledger

        entry = governance_ledger.record(
            thread_id="t1",
            run_id="r1",
            task_id="task1",
            source_agent="agent1",
            hook_name="test_hook",
            source_path="test.path",
            risk_level="medium",
            category="test",
            decision="ALLOW",
            tenant_id="tenant-a",
        )
        gov_id = entry["governance_id"]

        resp = client.get(f"/api/governance/{gov_id}")
        assert resp.status_code == 200
        assert resp.json()["governance_id"] == gov_id

    def test_other_tenant_gets_403(self):
        app = _make_app(tenant_id="tenant-b")
        client = TestClient(app)

        # Seed a governance entry for tenant-a
        from src.agents.governance.ledger import governance_ledger

        entry = governance_ledger.record(
            thread_id="t2",
            run_id="r2",
            task_id="task2",
            source_agent="agent2",
            hook_name="test_hook",
            source_path="test.path",
            risk_level="medium",
            category="test",
            decision="ALLOW",
            tenant_id="tenant-a",
        )
        gov_id = entry["governance_id"]

        resp = client.get(f"/api/governance/{gov_id}")
        assert resp.status_code == 403
        assert "another tenant" in resp.json()["detail"]

    def test_nonexistent_returns_404(self):
        app = _make_app(tenant_id="tenant-a")
        client = TestClient(app)
        resp = client.get("/api/governance/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_default_tenant_compatible(self):
        """OIDC disabled → tenant_id='default'. Entries recorded without
        explicit tenant_id default to 'default', so access should work."""
        app = _make_app(tenant_id="default")
        client = TestClient(app)

        from src.agents.governance.ledger import governance_ledger

        entry = governance_ledger.record(
            thread_id="t3",
            run_id="r3",
            task_id="task3",
            source_agent="agent3",
            hook_name="test_hook",
            source_path="test.path",
            risk_level="low",
            category="test",
            decision="ALLOW",
            # No explicit tenant_id → defaults to "default"
        )
        gov_id = entry["governance_id"]

        resp = client.get(f"/api/governance/{gov_id}")
        assert resp.status_code == 200


class TestGovernanceResolveTenantCheck:
    """POST /api/governance/{id}:resolve must enforce tenant ownership."""

    def test_other_tenant_resolve_gets_403(self):
        app = _make_app(tenant_id="tenant-c")
        client = TestClient(app)

        from src.agents.governance.ledger import governance_ledger

        entry = governance_ledger.record(
            thread_id="t4",
            run_id="r4",
            task_id="task4",
            source_agent="agent4",
            hook_name="test_hook",
            source_path="test.path",
            risk_level="high",
            category="test",
            decision="REQUIRE_INTERVENTION",
            tenant_id="tenant-a",
        )
        gov_id = entry["governance_id"]

        resp = client.post(f"/api/governance/{gov_id}:resolve", json={
            "action_key": "approve",
            "payload": {},
        })
        assert resp.status_code == 403
        assert "another tenant" in resp.json()["detail"]

    def test_own_tenant_resolve_passes_tenant_check(self):
        """Verify the tenant check passes — the resolve may still fail
        downstream (status check, LangGraph, etc.) but the 403 should NOT fire."""
        app = _make_app(tenant_id="tenant-d")
        client = TestClient(app)

        from src.agents.governance.ledger import governance_ledger

        entry = governance_ledger.record(
            thread_id="t5",
            run_id="r5",
            task_id="task5",
            source_agent="agent5",
            hook_name="test_hook",
            source_path="test.path",
            risk_level="high",
            category="test",
            decision="ALLOW",  # Not pending → will fail at status check, not tenant check
            tenant_id="tenant-d",
        )
        gov_id = entry["governance_id"]

        resp = client.post(f"/api/governance/{gov_id}:resolve", json={
            "action_key": "approve",
            "payload": {},
        })
        # Should be 409 (not pending), NOT 403 (tenant check passed)
        assert resp.status_code == 409


# ── User Profile Tenant Isolation ────────────────────────────────────────


class TestUserProfileTenantIsolation:
    """GET/PUT /api/user-profile must use tenant-scoped USER.md."""

    @pytest.fixture(autouse=True)
    def _setup_tmp(self, tmp_path: Path):
        """Patch get_paths to use a temp directory."""
        self.base_dir = tmp_path / ".deer-flow"
        self.base_dir.mkdir()

        from src.config.paths import Paths

        mock_paths = Paths(str(self.base_dir))
        self._patcher = patch("src.gateway.routers.agents.get_paths", return_value=mock_paths)
        self._patcher.start()
        yield
        self._patcher.stop()
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_default_tenant_uses_global_user_md(self):
        app = _make_app(tenant_id="default")
        client = TestClient(app)

        # Write via API
        resp = client.put("/api/user-profile", json={"content": "global profile"})
        assert resp.status_code == 200

        # Should be at base_dir/USER.md
        assert (self.base_dir / "USER.md").read_text(encoding="utf-8") == "global profile"

        # Read back
        resp = client.get("/api/user-profile")
        assert resp.status_code == 200
        assert resp.json()["content"] == "global profile"

    def test_tenant_a_uses_scoped_path(self):
        app = _make_app(tenant_id="tenant-a")
        client = TestClient(app)

        resp = client.put("/api/user-profile", json={"content": "tenant-a profile"})
        assert resp.status_code == 200

        # Should be at tenants/tenant-a/USER.md
        expected_path = self.base_dir / "tenants" / "tenant-a" / "USER.md"
        assert expected_path.exists()
        assert expected_path.read_text(encoding="utf-8") == "tenant-a profile"

        # Global USER.md should NOT exist
        assert not (self.base_dir / "USER.md").exists()

    def test_tenant_isolation(self):
        """Tenant A and Tenant B should not see each other's profile."""
        app_a = _make_app(tenant_id="tenant-a")
        app_b = _make_app(tenant_id="tenant-b")
        client_a = TestClient(app_a)
        client_b = TestClient(app_b)

        # Write profiles for both tenants
        client_a.put("/api/user-profile", json={"content": "profile A"})
        client_b.put("/api/user-profile", json={"content": "profile B"})

        # Each sees only their own
        assert client_a.get("/api/user-profile").json()["content"] == "profile A"
        assert client_b.get("/api/user-profile").json()["content"] == "profile B"

    def test_tenant_does_not_affect_default(self):
        """Writing to tenant-a should not create or modify global USER.md."""
        app_default = _make_app(tenant_id="default")
        app_a = _make_app(tenant_id="tenant-a")
        client_default = TestClient(app_default)
        client_a = TestClient(app_a)

        # Write global
        client_default.put("/api/user-profile", json={"content": "global"})
        # Write tenant-a
        client_a.put("/api/user-profile", json={"content": "A only"})

        # Global unchanged
        assert client_default.get("/api/user-profile").json()["content"] == "global"

    def test_nonexistent_profile_returns_null(self):
        app = _make_app(tenant_id="tenant-x")
        client = TestClient(app)

        resp = client.get("/api/user-profile")
        assert resp.status_code == 200
        assert resp.json()["content"] is None


# ── P1 fix: empty tenant_id normalisation ──────────────────────────────


class TestEmptyTenantIdNormalisation:
    """get_tenant_id must normalise empty/whitespace to 'default'."""

    def test_empty_string_normalised(self):
        """tenant_id='' must become 'default', not bypass filters."""
        app = _make_app(tenant_id="default")
        # Override to simulate empty string from OIDC middleware
        from src.gateway.dependencies import get_tenant_id

        app.dependency_overrides[get_tenant_id] = lambda: ""

        # get_tenant_id is overridden at dependency level, so we test the
        # function directly to confirm normalisation behaviour.
        from starlette.datastructures import State
        from fastapi import Request

        class FakeRequest:
            state = State()
        fake = FakeRequest()
        fake.state.tenant_id = ""

        result = get_tenant_id.__wrapped__(fake) if hasattr(get_tenant_id, "__wrapped__") else get_tenant_id(fake)
        assert result == "default"

    def test_whitespace_normalised(self):
        from starlette.datastructures import State

        class FakeRequest:
            state = State()
        fake = FakeRequest()
        fake.state.tenant_id = "   "

        from src.gateway.dependencies import get_tenant_id
        result = get_tenant_id(fake)
        assert result == "default"

    def test_none_normalised(self):
        from starlette.datastructures import State

        class FakeRequest:
            state = State()
        fake = FakeRequest()
        # No tenant_id attr at all

        from src.gateway.dependencies import get_tenant_id
        result = get_tenant_id(fake)
        assert result == "default"


# ── P2 fix: invalid tenant_id returns 400 ──────────────────────────────


class TestInvalidTenantIdReturns400:
    """Path traversal or illegal tenant IDs must return 400, not 500."""

    @pytest.fixture(autouse=True)
    def _setup_tmp(self, tmp_path: Path):
        self.base_dir = tmp_path / ".deer-flow"
        self.base_dir.mkdir()

        from src.config.paths import Paths

        mock_paths = Paths(str(self.base_dir))
        self._patcher = patch("src.gateway.routers.agents.get_paths", return_value=mock_paths)
        self._patcher.start()
        yield
        self._patcher.stop()

    def test_path_traversal_get_returns_400(self):
        app = _make_app(tenant_id="../evil")
        client = TestClient(app)
        resp = client.get("/api/user-profile")
        assert resp.status_code == 400
        assert "Invalid tenant_id" in resp.json()["detail"]

    def test_path_traversal_put_returns_400(self):
        app = _make_app(tenant_id="../evil")
        client = TestClient(app)
        resp = client.put("/api/user-profile", json={"content": "hack"})
        assert resp.status_code == 400
        assert "Invalid tenant_id" in resp.json()["detail"]

    def test_special_chars_returns_400(self):
        app = _make_app(tenant_id="tenant/../../etc")
        client = TestClient(app)
        resp = client.get("/api/user-profile")
        assert resp.status_code == 400

    def test_agents_list_returns_400(self):
        app = _make_app(tenant_id="../evil")
        client = TestClient(app)
        resp = client.get("/api/agents")
        assert resp.status_code == 400
        assert "Invalid tenant_id" in resp.json()["detail"]

    def test_agents_check_returns_400(self):
        app = _make_app(tenant_id="../evil")
        client = TestClient(app)
        resp = client.get("/api/agents/check?name=test-agent")
        assert resp.status_code == 400

    def test_agents_create_returns_400(self):
        app = _make_app(tenant_id="../evil")
        client = TestClient(app)
        resp = client.post("/api/agents", json={
            "name": "test-agent",
            "description": "test",
            "system_prompt": "test",
        })
        assert resp.status_code == 400


# ── Paths: tenant_user_md_file ───────────────────────────────────────────


class TestTenantUserMdFilePath:
    """Verify paths.tenant_user_md_file returns correct path."""

    def test_path_structure(self, tmp_path: Path):
        from src.config.paths import Paths

        p = Paths(str(tmp_path))
        result = p.tenant_user_md_file("org-1")
        assert result == tmp_path / "tenants" / "org-1" / "USER.md"
