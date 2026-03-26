"""Tests for Stage 5B governance operator console API endpoints.

Tests the queue, detail, history, and operator action API endpoints using
FastAPI's TestClient with a fresh governance ledger per test.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.agents.governance.engine import GovernanceEngine
from src.agents.governance.ledger import GovernanceLedger
from src.agents.governance.types import GovernanceDecision, RiskLevel
from src.gateway.routers.governance import router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    """Create a fresh ledger in a temp dir."""
    return GovernanceLedger(data_dir=str(tmp_path))


@pytest.fixture()
def engine(ledger):
    from src.agents.governance.policy import PolicyRegistry
    return GovernanceEngine(registry=PolicyRegistry(), ledger=ledger)


@pytest.fixture()
def client(ledger):
    """TestClient with the governance router, patching the global ledger."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    with patch("src.gateway.routers.governance.governance_ledger", ledger):
        yield TestClient(app)


def _seed_pending(engine, *, thread_id="th1", run_id="r1", task_id="t1",
                  agent="meeting-agent", request_id="intv_001",
                  risk_level=RiskLevel.HIGH):
    """Seed a pending_intervention entry in the ledger."""
    return engine.record_interrupt_emit(
        thread_id=thread_id,
        run_id=run_id,
        task_id=task_id,
        source_agent=agent,
        interrupt_type="intervention",
        source_path="executor.request_intervention",
        risk_level=risk_level,
        request_id=request_id,
        action_summary="Test intervention",
        metadata={
            "intervention_title": "Approve Meeting Cancel",
            "intervention_tool_name": "cancel_meeting",
            "intervention_display": [{"type": "text", "content": "Cancel this meeting?"}],
            "intervention_action_schema": {
                "actions": [
                    {"key": "approve", "label": "Approve", "kind": "confirm", "resolution_behavior": "resume_current_task"},
                    {"key": "reject", "label": "Reject", "kind": "confirm", "resolution_behavior": "fail_current_task"},
                ],
            },
            "intervention_fingerprint": "fp_test_001",
        },
    )


def _seed_decided(engine, *, thread_id="th1", run_id="r1"):
    """Seed an allow/decided entry in the ledger."""
    return engine._ledger.record(
        thread_id=thread_id,
        run_id=run_id,
        task_id="t2",
        source_agent="agent",
        hook_name="before_tool",
        source_path="middleware",
        risk_level=RiskLevel.MEDIUM,
        category="tool_execution",
        decision=GovernanceDecision.ALLOW,
        action_summary="Tool call: get_events",
    )


# ---------------------------------------------------------------------------
# Queue API tests
# ---------------------------------------------------------------------------

class TestQueueAPI:
    def test_empty_queue(self, client):
        resp = client.get("/api/governance/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_returns_pending_items(self, client, engine, ledger):
        gov_id = _seed_pending(engine)
        # Also seed a decided item — should NOT appear in queue
        _seed_decided(engine)

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        ids = [item["governance_id"] for item in data["items"]]
        assert gov_id in ids
        # All returned items must be pending
        for item in data["items"]:
            assert item["status"] == "pending_intervention"

    def test_filter_by_thread(self, client, engine, ledger):
        _seed_pending(engine, thread_id="th1", request_id="intv_a")
        _seed_pending(engine, thread_id="th2", request_id="intv_b")

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/queue", params={"thread_id": "th1"})

        data = resp.json()
        assert all(item["thread_id"] == "th1" for item in data["items"])

    def test_filter_by_risk_level(self, client, engine, ledger):
        _seed_pending(engine, risk_level=RiskLevel.HIGH, request_id="intv_h")
        _seed_pending(engine, risk_level=RiskLevel.CRITICAL, request_id="intv_c")

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/queue", params={"risk_level": "critical"})

        data = resp.json()
        assert all(item["risk_level"] == "critical" for item in data["items"])

    def test_pagination(self, client, engine, ledger):
        for i in range(5):
            _seed_pending(engine, request_id=f"intv_{i}")

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/queue", params={"limit": 2, "offset": 0})
            data = resp.json()
            assert len(data["items"]) == 2
            assert data["total"] == 5

    def test_queue_includes_detail_fields(self, client, engine, ledger):
        _seed_pending(engine)

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/queue")

        item = resp.json()["items"][0]
        # Queue items include intervention detail for rendering
        assert item["intervention_title"] == "Approve Meeting Cancel"
        assert item["intervention_tool_name"] == "cancel_meeting"


# ---------------------------------------------------------------------------
# History API tests
# ---------------------------------------------------------------------------

class TestHistoryAPI:
    def test_empty_history(self, client):
        resp = client.get("/api/governance/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []

    def test_returns_non_pending_items(self, client, engine, ledger):
        _seed_pending(engine, request_id="intv_p")
        decided_entry = _seed_decided(engine)

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/history")

        data = resp.json()
        ids = [item["governance_id"] for item in data["items"]]
        assert decided_entry["governance_id"] in ids
        # No pending items in history
        for item in data["items"]:
            assert item["status"] != "pending_intervention"

    def test_returns_resolved_items(self, client, engine, ledger):
        gov_id = _seed_pending(engine, request_id="intv_res")
        ledger.resolve(request_id="intv_res", status="resolved", resolved_by="operator")

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/history", params={"status": "resolved"})

        data = resp.json()
        ids = [item["governance_id"] for item in data["items"]]
        assert gov_id in ids
        assert all(item["status"] == "resolved" for item in data["items"])

    def test_rejects_pending_status_filter(self, client):
        resp = client.get("/api/governance/history", params={"status": "pending_intervention"})
        assert resp.status_code == 422

    def test_filter_by_thread(self, client, engine, ledger):
        _seed_decided(engine, thread_id="th1")
        _seed_decided(engine, thread_id="th2")

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/history", params={"thread_id": "th1"})

        data = resp.json()
        assert all(item["thread_id"] == "th1" for item in data["items"])

    def test_filter_by_created_from(self, client, engine, ledger):
        _seed_decided(engine)
        e2 = _seed_decided(engine)
        cutoff = e2["created_at"]

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/history", params={"created_from": cutoff})

        data = resp.json()
        assert all(item["created_at"] >= cutoff for item in data["items"])

    def test_filter_by_resolved_from_to(self, client, engine, ledger):
        _seed_pending(engine, request_id="intv_time")
        ledger.resolve(request_id="intv_time", status="resolved", resolved_by="operator")
        entry = ledger.get_by_request_id("intv_time")
        cutoff = entry["resolved_at"]

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get("/api/governance/history", params={
                "resolved_from": cutoff,
                "resolved_to": cutoff,
            })

        data = resp.json()
        assert len(data["items"]) >= 1
        assert all(item.get("resolved_at") for item in data["items"])


# ---------------------------------------------------------------------------
# Detail API tests
# ---------------------------------------------------------------------------

class TestDetailAPI:
    def test_get_existing_item(self, client, engine, ledger):
        gov_id = _seed_pending(engine)

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get(f"/api/governance/{gov_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["governance_id"] == gov_id
        assert data["status"] == "pending_intervention"
        # Detail includes intervention context
        assert data["intervention_title"] == "Approve Meeting Cancel"
        assert data["intervention_tool_name"] == "cancel_meeting"
        assert data["intervention_display"] is not None
        assert data["intervention_action_schema"] is not None
        assert data["intervention_fingerprint"] == "fp_test_001"

    def test_not_found(self, client):
        resp = client.get("/api/governance/gov_nonexistent")
        assert resp.status_code == 404

    def test_decided_item_detail(self, client, engine, ledger):
        entry = _seed_decided(engine)

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.get(f"/api/governance/{entry['governance_id']}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "decided"
        assert data["decision"] == "allow"


# ---------------------------------------------------------------------------
# Operator Resolve API tests
# ---------------------------------------------------------------------------

class TestOperatorResolveAPI:
    def test_not_found(self, client):
        resp = client.post(
            "/api/governance/gov_nonexistent:resolve",
            json={"action_key": "approve", "payload": {}, "fingerprint": "fp"},
        )
        assert resp.status_code == 404

    def test_not_pending(self, client, engine, ledger):
        entry = _seed_decided(engine)

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.post(
                f"/api/governance/{entry['governance_id']}:resolve",
                json={"action_key": "approve", "payload": {}},
            )

        assert resp.status_code == 409

    def test_missing_request_id(self, client, ledger):
        # Create entry with no request_id
        entry = ledger.record(
            thread_id="th1",
            run_id="r1",
            task_id="t1",
            source_agent="agent",
            hook_name="test",
            source_path="test",
            risk_level=RiskLevel.MEDIUM,
            category="test",
            decision=GovernanceDecision.REQUIRE_INTERVENTION,
        )

        with patch("src.gateway.routers.governance.governance_ledger", ledger):
            resp = client.post(
                f"/api/governance/{entry['governance_id']}:resolve",
                json={"action_key": "approve", "payload": {}},
            )

        assert resp.status_code == 422
        assert "request_id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Intervention context extraction tests
# ---------------------------------------------------------------------------

class TestInterventionContextExtraction:
    def test_extract_context_from_audit_hook(self):
        """Verify the audit hook extracts intervention context into metadata."""
        from src.agents.governance.audit_hooks import _extract_intervention_context

        proposed_update = {
            "task_pool": [{
                "task_id": "t1",
                "intervention_request": {
                    "request_id": "intv_001",
                    "title": "Approve Action",
                    "reason": "High risk",
                    "tool_name": "delete_meeting",
                    "fingerprint": "fp_123",
                    "display": [{"type": "text", "content": "Are you sure?"}],
                    "action_schema": {"actions": [{"key": "approve"}]},
                    "questions": [{"key": "q1", "kind": "input"}],
                },
            }],
        }
        ctx = _extract_intervention_context(proposed_update)
        assert ctx["intervention_title"] == "Approve Action"
        assert ctx["intervention_reason"] == "High risk"
        assert ctx["intervention_tool_name"] == "delete_meeting"
        assert ctx["intervention_fingerprint"] == "fp_123"
        assert ctx["intervention_display"] == [{"type": "text", "content": "Are you sure?"}]
        assert ctx["intervention_action_schema"] == {"actions": [{"key": "approve"}]}
        assert ctx["intervention_questions"] == [{"key": "q1", "kind": "input"}]

    def test_extract_context_no_intervention(self):
        from src.agents.governance.audit_hooks import _extract_intervention_context

        assert _extract_intervention_context({}) == {}
        assert _extract_intervention_context({"task_pool": [{"task_id": "t1"}]}) == {}

    def test_extract_context_partial(self):
        from src.agents.governance.audit_hooks import _extract_intervention_context

        ctx = _extract_intervention_context({
            "task_pool": [{
                "intervention_request": {
                    "request_id": "intv_002",
                    "title": "Partial",
                },
            }],
        })
        assert ctx["intervention_title"] == "Partial"
        assert "intervention_tool_name" not in ctx
