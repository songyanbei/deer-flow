"""Live end-to-end platform integration tests.

These tests hit the REAL Gateway (:8001) and LangGraph (:2024) services.
They require both services to be running locally with OIDC disabled (default).

Run with:
    PYTHONPATH=. uv run pytest tests/test_platform_e2e_live.py -v -s

Skip conditions:
    - Gateway (:8001) must be reachable
    - LangGraph (:2024) must be reachable
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

GATEWAY_URL = "http://127.0.0.1:8001"
LANGGRAPH_URL = "http://127.0.0.1:2024"


def _gateway_reachable() -> bool:
    try:
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _langgraph_reachable() -> bool:
    try:
        r = httpx.get(f"{LANGGRAPH_URL}/ok", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


skip_if_services_down = pytest.mark.skipif(
    not (_gateway_reachable() and _langgraph_reachable()),
    reason="Gateway (:8001) and/or LangGraph (:2024) not running",
)


@pytest.fixture(scope="module")
def gw():
    """Shared httpx client pointed at the Gateway."""
    with httpx.Client(base_url=GATEWAY_URL, timeout=60) as client:
        yield client


# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_sse_events(text: str) -> list[dict]:
    """Parse raw SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = []

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


# ── Tests ────────────────────────────────────────────────────────────────


@skip_if_services_down
class TestLiveAgentSync:
    """Stage B: Batch agent sync against real Gateway."""

    def test_sync_upsert_creates_agents(self, gw: httpx.Client):
        resp = gw.post("/api/agents/sync", json={
            "agents": [
                {
                    "name": "e2e-research",
                    "domain": "research",
                    "description": "E2E test research agent",
                    "soul": "You are a research assistant for e2e testing.",
                },
                {
                    "name": "e2e-analyst",
                    "domain": "analytics",
                    "description": "E2E test analyst agent",
                    "soul": "You are a data analyst for e2e testing.",
                },
            ],
            "mode": "upsert",
        })
        assert resp.status_code == 200, f"Unexpected: {resp.text}"
        data = resp.json()
        # Agents should be created or updated (idempotent)
        synced = set(data["created"] + data["updated"])
        assert "e2e-research" in synced
        assert "e2e-analyst" in synced
        assert data["errors"] == []

    def test_synced_agents_appear_in_list(self, gw: httpx.Client):
        resp = gw.get("/api/agents")
        assert resp.status_code == 200
        names = {a["name"] for a in resp.json()["agents"]}
        assert "e2e-research" in names
        assert "e2e-analyst" in names

    def test_synced_agent_details_correct(self, gw: httpx.Client):
        resp = gw.get("/api/agents/e2e-research")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "research"
        assert data["description"] == "E2E test research agent"
        assert "e2e testing" in data["soul"].lower()


@skip_if_services_down
class TestLiveRuntimeThread:
    """Stage A: Thread creation against real LangGraph."""

    def test_create_runtime_thread(self, gw: httpx.Client):
        resp = gw.post("/api/runtime/threads", json={
            "portal_session_id": f"e2e_sess_{int(time.time())}",
        })
        assert resp.status_code == 200, f"Unexpected: {resp.text}"
        data = resp.json()
        assert "thread_id" in data
        assert data["tenant_id"] == "default"
        assert data["user_id"] == "anonymous"
        assert "created_at" in data

        # Store thread_id for subsequent tests
        TestLiveRuntimeThread._thread_id = data["thread_id"]
        TestLiveRuntimeThread._portal_session_id = data["portal_session_id"]

    def test_get_runtime_thread(self, gw: httpx.Client):
        thread_id = getattr(TestLiveRuntimeThread, "_thread_id", None)
        if not thread_id:
            pytest.skip("No thread created in previous test")

        resp = gw.get(f"/api/runtime/threads/{thread_id}")
        assert resp.status_code == 200, f"Unexpected: {resp.text}"
        data = resp.json()
        assert data["thread_id"] == thread_id
        assert data["tenant_id"] == "default"
        assert data["state"] is not None
        # Fresh thread — no artifacts or interventions
        assert data["state"]["artifacts_count"] == 0
        assert data["state"]["pending_intervention"] is False

    def test_get_nonexistent_thread_404(self, gw: httpx.Client):
        resp = gw.get("/api/runtime/threads/nonexistent-thread-id-12345")
        assert resp.status_code == 404


@skip_if_services_down
class TestLiveRuntimeValidation:
    """Payload validation against real Gateway (no LangGraph needed for 422s)."""

    _thread_id: str | None = None

    @pytest.fixture(autouse=True)
    def _ensure_thread(self, gw: httpx.Client):
        if TestLiveRuntimeValidation._thread_id is None:
            resp = gw.post("/api/runtime/threads", json={
                "portal_session_id": f"e2e_val_{int(time.time())}",
            })
            if resp.status_code == 200:
                TestLiveRuntimeValidation._thread_id = resp.json()["thread_id"]

    def test_empty_message_rejected(self, gw: httpx.Client):
        tid = self._thread_id
        if not tid:
            pytest.skip("No thread")
        resp = gw.post(f"/api/runtime/threads/{tid}/messages:stream", json={
            "message": "   ",
            "group_key": "team",
            "allowed_agents": ["e2e-research"],
        })
        assert resp.status_code == 422

    def test_empty_allowed_agents_rejected(self, gw: httpx.Client):
        tid = self._thread_id
        if not tid:
            pytest.skip("No thread")
        resp = gw.post(f"/api/runtime/threads/{tid}/messages:stream", json={
            "message": "hello",
            "group_key": "team",
            "allowed_agents": [],
        })
        assert resp.status_code == 422

    def test_unknown_agent_rejected(self, gw: httpx.Client):
        tid = self._thread_id
        if not tid:
            pytest.skip("No thread")
        resp = gw.post(f"/api/runtime/threads/{tid}/messages:stream", json={
            "message": "hello",
            "group_key": "team",
            "allowed_agents": ["definitely-not-a-real-agent-xyz"],
        })
        assert resp.status_code == 422
        assert "definitely-not-a-real-agent-xyz" in resp.json()["detail"]

    def test_entry_agent_must_be_in_allowed(self, gw: httpx.Client):
        tid = self._thread_id
        if not tid:
            pytest.skip("No thread")
        resp = gw.post(f"/api/runtime/threads/{tid}/messages:stream", json={
            "message": "hello",
            "group_key": "team",
            "allowed_agents": ["e2e-research"],
            "entry_agent": "e2e-analyst",
        })
        assert resp.status_code == 422
        assert "entry_agent" in resp.json()["detail"]

    def test_invalid_orchestration_mode_rejected(self, gw: httpx.Client):
        tid = self._thread_id
        if not tid:
            pytest.skip("No thread")
        resp = gw.post(f"/api/runtime/threads/{tid}/messages:stream", json={
            "message": "hello",
            "group_key": "team",
            "allowed_agents": ["e2e-research"],
            "requested_orchestration_mode": "bogus",
        })
        assert resp.status_code == 422

    def test_non_primitive_metadata_rejected(self, gw: httpx.Client):
        tid = self._thread_id
        if not tid:
            pytest.skip("No thread")
        resp = gw.post(f"/api/runtime/threads/{tid}/messages:stream", json={
            "message": "hello",
            "group_key": "team",
            "allowed_agents": ["e2e-research"],
            "metadata": {"nested": {"key": "value"}},
        })
        assert resp.status_code == 422


@skip_if_services_down
class TestLiveMessageStream:
    """Stage A: Full message stream against real LangGraph + Gateway.

    This is the real end-to-end test: a message is submitted to the runtime,
    LangGraph executes the multi-agent workflow, and the Gateway normalizes
    the SSE response.
    """

    def test_stream_message_e2e(self, gw: httpx.Client):
        # Step 1: Create a fresh thread
        create_resp = gw.post("/api/runtime/threads", json={
            "portal_session_id": f"e2e_stream_{int(time.time())}",
        })
        assert create_resp.status_code == 200
        thread_id = create_resp.json()["thread_id"]

        # Step 2: Stream a message with allowed_agents
        stream_resp = gw.post(
            f"/api/runtime/threads/{thread_id}/messages:stream",
            json={
                "message": "请用一句话介绍你自己",
                "group_key": "e2e-team",
                "allowed_agents": ["e2e-research", "e2e-analyst"],
                "entry_agent": "e2e-research",
                "requested_orchestration_mode": "auto",
            },
            headers={"Accept": "text/event-stream"},
        )
        assert stream_resp.status_code == 200, f"Stream failed: {stream_resp.text[:500]}"
        assert "text/event-stream" in stream_resp.headers.get("content-type", "")

        # Step 3: Parse SSE events
        events = _parse_sse_events(stream_resp.text)
        event_names = [e["event"] for e in events]

        # Must have ack as first event
        assert len(events) > 0, "No SSE events received"
        assert events[0]["event"] == "ack"
        assert events[0]["data"]["thread_id"] == thread_id

        # Must end with run_completed or run_failed
        terminal_events = {"run_completed", "run_failed"}
        assert event_names[-1] in terminal_events, f"Last event was '{event_names[-1]}', expected terminal event"

        # No raw upstream event names should be leaked
        for name in event_names:
            assert name != "values", "Raw 'values' event leaked"
            assert not name.startswith("messages/"), f"Raw '{name}' event leaked"

        # Print summary for manual inspection
        print(f"\n  Thread: {thread_id}")
        print(f"  Events received: {len(events)}")
        print(f"  Event types: {event_names}")
        if events[-1]["event"] == "run_completed":
            print("  Result: SUCCESS")
        else:
            print(f"  Result: FAILED - {events[-1]['data'].get('error', 'unknown')}")

        # Step 4: Verify binding was updated
        get_resp = gw.get(f"/api/runtime/threads/{thread_id}")
        assert get_resp.status_code == 200
        binding = get_resp.json()
        assert binding["group_key"] == "e2e-team"
        assert binding["allowed_agents"] == ["e2e-research", "e2e-analyst"]
        assert binding["entry_agent"] == "e2e-research"
        assert binding["requested_orchestration_mode"] == "auto"

        # Store for cleanup
        TestLiveMessageStream._thread_id = thread_id


@skip_if_services_down
class TestLiveCleanup:
    """Clean up e2e test agents."""

    def test_cleanup_e2e_agents(self, gw: httpx.Client):
        for name in ("e2e-research", "e2e-analyst"):
            resp = gw.delete(f"/api/agents/{name}")
            assert resp.status_code in (204, 404), f"Failed to delete {name}: {resp.text}"
