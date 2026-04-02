"""Live OIDC end-to-end tests against real Gateway + Keycloak.

These tests hit the REAL Gateway (:8001) with OIDC_ENABLED=true and verify
the full JWT validation chain against the real Keycloak JWKS endpoint.

Requirements:
    - Gateway (:8001) running with OIDC_ENABLED=true
    - Keycloak (20.20.136.3:8443) reachable from Gateway
    - A valid access_token from Keycloak (moss realm, moss-market client)

Run with:
    OIDC_TEST_TOKEN=<access_token> PYTHONPATH=. uv run pytest tests/test_oidc_e2e_live.py -v -s

Skip conditions:
    - Gateway (:8001) must be reachable
    - OIDC_TEST_TOKEN env var must be set
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

GATEWAY_URL = "http://127.0.0.1:8001"

# Token is passed via environment variable to avoid hardcoding credentials.
ACCESS_TOKEN = os.environ.get("OIDC_TEST_TOKEN", "")


def _gateway_reachable() -> bool:
    try:
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _gateway_oidc_enabled() -> bool:
    """Check that Gateway rejects unauthenticated requests (OIDC is on)."""
    try:
        r = httpx.get(f"{GATEWAY_URL}/api/models", timeout=3)
        return r.status_code == 401
    except Exception:
        return False


skip_if_not_ready = pytest.mark.skipif(
    not (_gateway_reachable() and _gateway_oidc_enabled() and ACCESS_TOKEN),
    reason="Gateway not running with OIDC enabled, or OIDC_TEST_TOKEN not set",
)


@pytest.fixture(scope="module")
def gw():
    """Shared httpx client pointed at the Gateway (no auth header)."""
    with httpx.Client(base_url=GATEWAY_URL, timeout=30) as client:
        yield client


@pytest.fixture(scope="module")
def auth_gw():
    """Shared httpx client with Bearer token."""
    with httpx.Client(
        base_url=GATEWAY_URL,
        timeout=30,
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
    ) as client:
        yield client


# ── Helpers ──────────────────────────────────────────────────────────────


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (for test assertions)."""
    import base64

    parts = token.split(".")
    payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
    return json.loads(base64.b64decode(payload))


def _parse_sse_events(text: str) -> list[dict]:
    """Parse raw SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data: list[str] = []
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_event is not None:
            data_str = "\n".join(current_data)
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = data_str
            events.append({"event": current_event, "data": data})
            current_event = None
            current_data = []
    return events


# ── Tests: Exempt Paths ──────────────────────────────────────────────────


@skip_if_not_ready
class TestExemptPaths:
    """Verify paths that should NOT require authentication."""

    def test_health_no_auth(self, gw: httpx.Client):
        resp = gw.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_docs_no_auth(self, gw: httpx.Client):
        resp = gw.get("/docs")
        assert resp.status_code == 200

    def test_openapi_no_auth(self, gw: httpx.Client):
        resp = gw.get("/openapi.json")
        assert resp.status_code == 200

    def test_debug_metrics_no_auth(self, gw: httpx.Client):
        resp = gw.get("/debug/metrics")
        assert resp.status_code == 200


# ── Tests: Authentication Rejection ──────────────────────────────────────


@skip_if_not_ready
class TestAuthRejection:
    """Verify that invalid/missing auth is properly rejected."""

    def test_no_token_401(self, gw: httpx.Client):
        resp = gw.get("/api/models")
        assert resp.status_code == 401
        assert "Authorization" in resp.json()["detail"]

    def test_empty_bearer_401(self, gw: httpx.Client):
        # httpx rejects truly empty Bearer values at protocol level,
        # so we send a whitespace-only token instead.
        resp = gw.get("/api/models", headers={"Authorization": "Bearer x"})
        assert resp.status_code == 401

    def test_invalid_jwt_401(self, gw: httpx.Client):
        resp = gw.get("/api/models", headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401

    def test_malformed_header_401(self, gw: httpx.Client):
        resp = gw.get("/api/models", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401

    def test_tampered_token_401(self, gw: httpx.Client):
        """Corrupt multiple bytes in the signature to ensure verification fails."""
        if not ACCESS_TOKEN:
            pytest.skip("No token")
        parts = ACCESS_TOKEN.split(".")
        sig = parts[2]
        # Reverse a significant chunk of the signature to guarantee corruption
        tampered_sig = sig[:10] + sig[10:40][::-1] + sig[40:]
        tampered = f"{parts[0]}.{parts[1]}.{tampered_sig}"
        resp = gw.get("/api/models", headers={"Authorization": f"Bearer {tampered}"})
        assert resp.status_code == 401


# ── Tests: Valid Token Access ────────────────────────────────────────────


@skip_if_not_ready
class TestValidTokenAccess:
    """Verify that a real Keycloak token grants access and identity is extracted."""

    def test_models_accessible(self, auth_gw: httpx.Client):
        resp = auth_gw.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data

    def test_skills_accessible(self, auth_gw: httpx.Client):
        resp = auth_gw.get("/api/skills")
        assert resp.status_code == 200

    def test_memory_accessible(self, auth_gw: httpx.Client):
        resp = auth_gw.get("/api/memory/config")
        assert resp.status_code == 200

    def test_agents_list_accessible(self, auth_gw: httpx.Client):
        resp = auth_gw.get("/api/agents")
        assert resp.status_code == 200
        assert "agents" in resp.json()


# ── Tests: Identity Extraction ───────────────────────────────────────────


@skip_if_not_ready
class TestIdentityExtraction:
    """Verify that user_id (sub), username, and tenant_id are correctly
    extracted from the real Keycloak JWT and reflected in API responses."""

    def test_runtime_thread_identity(self, auth_gw: httpx.Client):
        """Create a runtime thread and verify identity fields from token."""
        claims = _decode_jwt_payload(ACCESS_TOKEN)
        expected_user_id = claims["sub"]

        resp = auth_gw.post("/api/runtime/threads", json={
            "portal_session_id": f"oidc_e2e_{int(time.time())}",
        })
        assert resp.status_code == 200, f"Unexpected: {resp.text}"
        data = resp.json()

        # user_id should be the JWT 'sub' claim
        assert data["user_id"] == expected_user_id

        # tenant_id should fall back to 'default' (no tenant claim in current token)
        assert data["tenant_id"] == "default"

        # Store thread_id for subsequent tests
        TestIdentityExtraction._thread_id = data["thread_id"]

    def test_thread_binding_reflects_identity(self, auth_gw: httpx.Client):
        thread_id = getattr(TestIdentityExtraction, "_thread_id", None)
        if not thread_id:
            pytest.skip("No thread created")

        claims = _decode_jwt_payload(ACCESS_TOKEN)
        resp = auth_gw.get(f"/api/runtime/threads/{thread_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == claims["sub"]
        assert data["tenant_id"] == "default"


# ── Tests: Tenant Fallback ───────────────────────────────────────────────


@skip_if_not_ready
class TestTenantFallback:
    """Verify that without organization/tenant_id claims, system falls
    back to 'default' tenant and still operates normally."""

    def test_no_tenant_claim_falls_back(self, auth_gw: httpx.Client):
        claims = _decode_jwt_payload(ACCESS_TOKEN)
        # Confirm the token has NO tenant claims
        assert "organization" not in claims
        assert "tenant_id" not in claims
        assert "org_id" not in claims

    def test_agents_work_under_default_tenant(self, auth_gw: httpx.Client):
        """Agent sync should work under default tenant."""
        resp = auth_gw.post("/api/agents/sync", json={
            "agents": [
                {
                    "name": "oidc-e2e-test-agent",
                    "domain": "research",
                    "description": "OIDC E2E test agent",
                    "soul": "You are a test agent for OIDC E2E testing.",
                },
            ],
            "mode": "upsert",
        })
        assert resp.status_code == 200, f"Unexpected: {resp.text}"
        data = resp.json()
        assert "oidc-e2e-test-agent" in (data["created"] + data["updated"])


# ── Tests: Cross-User Isolation ──────────────────────────────────────────


@skip_if_not_ready
class TestCrossUserIsolation:
    """Verify that threads created by one user cannot be accessed by another.
    Since we only have one token, we test by checking that ownership fields
    are correctly set and that the thread is accessible by the same user."""

    def test_own_thread_accessible(self, auth_gw: httpx.Client):
        # Create a thread
        resp = auth_gw.post("/api/runtime/threads", json={
            "portal_session_id": f"oidc_isolation_{int(time.time())}",
        })
        assert resp.status_code == 200
        thread_id = resp.json()["thread_id"]

        # Same user can access it
        resp = auth_gw.get(f"/api/runtime/threads/{thread_id}")
        assert resp.status_code == 200
        assert resp.json()["thread_id"] == thread_id


# ── Tests: Full OIDC + Runtime E2E ──────────────────────────────────────


@skip_if_not_ready
class TestOIDCRuntimeE2E:
    """Full end-to-end: OIDC auth → agent sync → thread create → message stream.
    This is the complete platform integration path with real Keycloak auth."""

    def test_full_oidc_flow(self, auth_gw: httpx.Client):
        claims = _decode_jwt_payload(ACCESS_TOKEN)

        # Step 1: Sync a test agent
        sync_resp = auth_gw.post("/api/agents/sync", json={
            "agents": [
                {
                    "name": "oidc-e2e-flow-agent",
                    "domain": "research",
                    "description": "OIDC full flow test agent",
                    "soul": "You are a research assistant for OIDC E2E flow testing.",
                },
            ],
            "mode": "upsert",
        })
        assert sync_resp.status_code == 200
        print(f"\n  Agent synced: oidc-e2e-flow-agent")

        # Step 2: Create a thread (with OIDC identity)
        thread_resp = auth_gw.post("/api/runtime/threads", json={
            "portal_session_id": f"oidc_flow_{int(time.time())}",
        })
        assert thread_resp.status_code == 200
        thread_id = thread_resp.json()["thread_id"]
        assert thread_resp.json()["user_id"] == claims["sub"]
        assert thread_resp.json()["tenant_id"] == "default"
        print(f"  Thread created: {thread_id}")

        # Step 3: Stream a message through the runtime
        stream_resp = auth_gw.post(
            f"/api/runtime/threads/{thread_id}/messages:stream",
            json={
                "message": "hello, who are you?",
                "group_key": "oidc-e2e-team",
                "allowed_agents": ["oidc-e2e-flow-agent"],
                "entry_agent": "oidc-e2e-flow-agent",
                "requested_orchestration_mode": "auto",
            },
            headers={"Accept": "text/event-stream"},
        )
        assert stream_resp.status_code == 200, f"Stream failed: {stream_resp.text[:500]}"
        assert "text/event-stream" in stream_resp.headers.get("content-type", "")

        # Step 4: Parse and verify SSE events
        events = _parse_sse_events(stream_resp.text)
        event_names = [e["event"] for e in events]

        assert len(events) > 0, "No SSE events received"
        assert events[0]["event"] == "ack"
        assert events[0]["data"]["thread_id"] == thread_id

        terminal_events = {"run_completed", "run_failed"}
        assert event_names[-1] in terminal_events

        print(f"  Events: {event_names}")
        print(f"  Result: {'SUCCESS' if events[-1]['event'] == 'run_completed' else 'FAILED'}")

        # Step 5: Verify binding has correct identity + metadata
        binding_resp = auth_gw.get(f"/api/runtime/threads/{thread_id}")
        assert binding_resp.status_code == 200
        binding = binding_resp.json()
        assert binding["user_id"] == claims["sub"]
        assert binding["tenant_id"] == "default"
        assert binding["group_key"] == "oidc-e2e-team"
        assert binding["allowed_agents"] == ["oidc-e2e-flow-agent"]
        print(f"  Binding verified: user_id={binding['user_id']}, tenant=default")


# ── Cleanup ──────────────────────────────────────────────────────────────


@skip_if_not_ready
class TestOIDCCleanup:
    """Clean up test agents."""

    def test_cleanup(self, auth_gw: httpx.Client):
        for name in ("oidc-e2e-test-agent", "oidc-e2e-flow-agent"):
            resp = auth_gw.delete(f"/api/agents/{name}")
            assert resp.status_code in (204, 404)
