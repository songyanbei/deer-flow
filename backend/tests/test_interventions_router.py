from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.routers import interventions


def _make_app():
    app = FastAPI()
    app.include_router(interventions.router)
    return app


def _pending_task(
    *,
    request_id: str = "intv-1",
    fingerprint: str = "fp-1",
    action_key: str = "approve",
    resolution_behavior: str = "resume_current_task",
):
    return {
        "task_id": "task-1",
        "description": "execute risky tool",
        "status": "WAITING_INTERVENTION",
        "intervention_status": "pending",
        "intervention_request": {
            "request_id": request_id,
            "fingerprint": fingerprint,
            "action_schema": {
                "actions": [
                    {
                        "key": action_key,
                        "label": action_key,
                        "kind": "button",
                        "resolution_behavior": resolution_behavior,
                    }
                ]
            },
        },
        "resolved_inputs": {},
    }


def _mock_langgraph_client(*, state_values: dict):
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"thread_id": "thread-1"})
    client.threads.get_state = AsyncMock(return_value={"values": state_values})
    client.threads.update_state = AsyncMock(return_value=None)
    client.runs.create = AsyncMock(return_value={"run_id": "resume-1"})
    return client


def test_resolve_intervention_accepts_valid_resume_request():
    task = _pending_task()
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "approve",
                    "payload": {"comment": "go ahead"},
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["thread_id"] == "thread-1"
    assert data["request_id"] == "intv-1"
    assert data["fingerprint"] == "fp-1"
    assert data["accepted"] is True
    assert data["resume_action"] == "submit_resume"
    assert data["resume_payload"]["message"].startswith("[intervention_resolved]")
    client_mock.threads.update_state.assert_awaited_once()
    updated_task = client_mock.threads.update_state.await_args.kwargs["values"]["task_pool"][0]
    assert updated_task["status"] == "RUNNING"
    assert updated_task["intervention_status"] == "resolved"
    assert updated_task["resolved_inputs"]["intervention_resolution"]["payload"] == {"comment": "go ahead"}


def test_resolve_intervention_marks_task_failed_for_reject_action():
    task = _pending_task(action_key="reject", resolution_behavior="fail_current_task")
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "reject",
                    "payload": {"comment": "stop it"},
                },
            )

    assert response.status_code == 200
    updated_task = client_mock.threads.update_state.await_args.kwargs["values"]["task_pool"][0]
    assert updated_task["status"] == "FAILED"
    assert updated_task["status_detail"] == "@failed"
    assert "Intervention rejected by user" in updated_task["error"]
    client_mock.runs.create.assert_not_awaited()


def test_resolve_intervention_rejects_fingerprint_mismatch():
    task = _pending_task()
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "stale-fp",
                    "action_key": "approve",
                    "payload": {},
                },
            )

    assert response.status_code == 409
    assert "Fingerprint mismatch" in response.json()["detail"]
    client_mock.threads.update_state.assert_not_awaited()


def test_resolve_intervention_rejects_unknown_action_key():
    task = _pending_task()
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "override",
                    "payload": {"comment": "new plan"},
                },
            )

    assert response.status_code == 422
    assert "Invalid action_key" in response.json()["detail"]
    client_mock.threads.update_state.assert_not_awaited()


def test_resolve_intervention_requires_payload_object():
    with TestClient(_make_app()) as client:
        response = client.post(
            "/api/threads/thread-1/interventions/intv-1:resolve",
            json={
                "fingerprint": "fp-1",
                "action_key": "approve",
            },
        )

    assert response.status_code == 422
