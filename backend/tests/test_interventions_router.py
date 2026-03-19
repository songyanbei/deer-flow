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
    action_kind: str = "button",
    resolution_behavior: str = "resume_current_task",
    options: list[str] | None = None,
    required: bool | None = None,
    min_select: int | None = None,
    max_select: int | None = None,
    intervention_type: str = "before_tool",
):
    action = {
        "key": action_key,
        "label": action_key,
        "kind": action_kind,
        "resolution_behavior": resolution_behavior,
    }
    if options is not None:
        action["options"] = options
    if required is not None:
        action["required"] = required
    if min_select is not None:
        action["min_select"] = min_select
    if max_select is not None:
        action["max_select"] = max_select
    return {
        "task_id": "task-1",
        "description": "execute risky tool",
        "status": "WAITING_INTERVENTION",
        "intervention_status": "pending",
        "intervention_request": {
            "request_id": request_id,
            "fingerprint": fingerprint,
            "semantic_key": fingerprint,
            "interrupt_kind": "before_tool" if intervention_type == "before_tool" else "selection",
            "source_signal": "intervention_required" if intervention_type == "before_tool" else "request_help",
            "intervention_type": intervention_type,
            "source_agent": "meeting-agent",
            "source_task_id": "task-1",
            "tool_name": "book_room",
            "context": {"tool_args": {"room": "Room A"}},
            "action_schema": {
                "actions": [action]
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
    updated_values = client_mock.threads.update_state.await_args.kwargs["values"]
    updated_task = client_mock.threads.update_state.await_args.kwargs["values"]["task_pool"][0]
    assert updated_task["status"] == "RUNNING"
    assert updated_task["intervention_status"] == "resolved"
    assert updated_task["intervention_resolution"]["request_id"] == "intv-1"
    assert updated_task["intervention_resolution"]["fingerprint"] == "fp-1"
    assert updated_task["intervention_resolution"]["resolution_behavior"] == "resume_current_task"
    assert updated_task["resolved_inputs"]["intervention_resolution"]["payload"] == {"comment": "go ahead"}
    assert updated_task["resolved_inputs"]["intervention_resolution"]["request_id"] == "intv-1"
    assert updated_values["intervention_cache"]


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
    assert updated_task["intervention_resolution"]["resolution_behavior"] == "fail_current_task"
    assert client_mock.threads.update_state.await_args.kwargs["values"]["intervention_cache"]
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


def test_resolve_intervention_returns_checkpoint_when_update_state_provides_it():
    task = _pending_task()
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    client_mock.threads.update_state = AsyncMock(return_value={"checkpoint": {"thread_ts": "cp-1"}})
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
    assert response.json()["checkpoint"] == {"thread_ts": "cp-1"}


def test_resolve_intervention_accepts_single_select_payload():
    task = _pending_task(
        action_key="submit_response",
        action_kind="single_select",
        options=["Room A", "Room B"],
    )
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "submit_response",
                    "payload": {"selected": "Room A"},
                },
            )

    assert response.status_code == 200
    updated_task = client_mock.threads.update_state.await_args.kwargs["values"]["task_pool"][0]
    assert updated_task["resolved_inputs"]["intervention_resolution"]["payload"] == {"selected": "Room A"}


def test_resolve_intervention_accepts_multi_select_payload():
    task = _pending_task(
        action_key="submit_response",
        action_kind="multi_select",
        options=["Alice", "Bob", "Charlie"],
        min_select=1,
        max_select=2,
    )
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "submit_response",
                    "payload": {"selected": ["Alice", "Bob"]},
                },
            )

    assert response.status_code == 200
    updated_task = client_mock.threads.update_state.await_args.kwargs["values"]["task_pool"][0]
    assert updated_task["resolved_inputs"]["intervention_resolution"]["payload"] == {
        "selected": ["Alice", "Bob"]
    }


def test_resolve_intervention_accepts_confirm_payload():
    task = _pending_task(
        action_key="approve",
        action_kind="confirm",
    )
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "approve",
                    "payload": {"confirmed": True},
                },
            )

    assert response.status_code == 200


def test_resolve_intervention_accepts_input_payload():
    task = _pending_task(
        action_key="submit_response",
        action_kind="input",
    )
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "submit_response",
                    "payload": {"text": "Project kickoff"},
                },
            )

    assert response.status_code == 200


def test_resolve_intervention_writes_cache_for_input_clarification():
    task = _pending_task(
        request_id="intv-clar-1",
        fingerprint="clar-fp-1",
        action_key="submit_response",
        action_kind="input",
        intervention_type="clarification",
    )
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task]})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/intv-clar-1:resolve",
                json={
                    "fingerprint": "clar-fp-1",
                    "action_key": "submit_response",
                    "payload": {"text": "Project kickoff"},
                },
            )

    assert response.status_code == 200
    cache = client_mock.threads.update_state.await_args.kwargs["values"]["intervention_cache"]
    assert cache["clar-fp-1"]["intervention_type"] == "clarification"
    assert cache["clar-fp-1"]["max_reuse"] == -1
    assert cache["clar-fp-1"]["payload"] == {"text": "Project kickoff"}


def test_resolve_intervention_rejects_duplicate_submit_when_task_already_resolved():
    resolved_task = {
        **_pending_task(),
        "status": "RUNNING",
        "intervention_status": "resolved",
    }
    client_mock = _mock_langgraph_client(state_values={"task_pool": [resolved_task]})
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

    assert response.status_code == 404
    assert "No pending intervention found" in response.json()["detail"]
    client_mock.threads.update_state.assert_not_awaited()
