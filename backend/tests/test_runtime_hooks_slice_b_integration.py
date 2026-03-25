from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, ToolMessage

from src.agents.executor.executor import _mcp_initialized, executor_node
from src.agents.hooks import (
    RuntimeHookContext,
    RuntimeHookHandler,
    RuntimeHookName,
    RuntimeHookResult,
    runtime_hook_registry,
)
from src.agents.router.semantic_router import router_node
from src.gateway.routers import interventions
from src.observability.node_wrapper import traced_node


@pytest.fixture(autouse=True)
def _clear_runtime_hooks():
    runtime_hook_registry.clear()
    yield
    runtime_hook_registry.clear()


class PatchCurrentUpdateHook(RuntimeHookHandler):
    def __init__(self, name: str, order: list[str], patch_key: str):
        self.name = name
        self.priority = 100
        self._order = order
        self._patch_key = patch_key

    def handle(self, ctx: RuntimeHookContext) -> RuntimeHookResult:
        self._order.append(self.name)
        patch = {self._patch_key: True}

        task_pool = ctx.proposed_update.get("task_pool")
        if isinstance(task_pool, list) and task_pool and isinstance(task_pool[0], dict):
            patch["task_pool"] = [{**task_pool[0], self._patch_key: True}]

        return RuntimeHookResult.ok(patch=patch, reason=f"patched_by_{self.name}")


def _pending_intervention_task() -> dict:
    return {
        "task_id": "task-1",
        "description": "execute risky tool",
        "status": "WAITING_INTERVENTION",
        "run_id": "run-1",
        "assigned_agent": "meeting-agent",
        "intervention_status": "pending",
        "intervention_request": {
            "request_id": "req-1",
            "fingerprint": "fp-1",
            "semantic_key": "fp-1",
            "interrupt_kind": "before_tool",
            "source_signal": "intervention_required",
            "intervention_type": "before_tool",
            "source_agent": "meeting-agent",
            "source_task_id": "task-1",
            "action_schema": {
                "actions": [
                    {
                        "key": "approve",
                        "label": "Approve",
                        "kind": "button",
                        "resolution_behavior": "resume_current_task",
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


def _make_app():
    app = FastAPI()
    app.include_router(interventions.router)
    return app


def test_executor_before_interrupt_emit_hook_patches_return_value(monkeypatch):
    class ClarifyingAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "messages": [
                    ToolMessage(
                        content="Please confirm the employee identity.",
                        tool_call_id="clarify-1",
                        name="ask_clarification",
                    )
                ]
            }

    order: list[str] = []
    runtime_hook_registry.register(
        RuntimeHookName.BEFORE_INTERRUPT_EMIT,
        PatchCurrentUpdateHook("before_interrupt_emit", order, "interrupt_hook_seen"),
    )

    def _make_lead_agent(_config):
        return ClarifyingAgent()

    monkeypatch.setattr("src.agents.executor.executor.load_agent_config", lambda _name: SimpleNamespace(mcp_servers=[]))
    monkeypatch.setattr("src.agents.lead_agent.agent.make_lead_agent", _make_lead_agent)
    _mcp_initialized.clear()

    async def _run():
        with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
            with patch("src.agents.executor.executor.get_stream_writer", return_value=lambda _event: None):
                return await executor_node(
                    {
                        "run_id": "run-1",
                        "task_pool": [
                            {
                                "task_id": "task-1",
                                "description": "lookup employee id",
                                "run_id": "run-1",
                                "assigned_agent": "contacts-agent",
                                "status": "RUNNING",
                            }
                        ],
                        "verified_facts": {},
                    },
                    {"configurable": {"thread_id": "thread-1"}},
                )

    result = asyncio.run(_run())

    assert order == ["before_interrupt_emit"]
    assert result["interrupt_hook_seen"] is True
    assert result["task_pool"][0]["interrupt_hook_seen"] is True
    assert result["execution_state"] == "INTERRUPTED"


def test_router_after_interrupt_resolve_hook_patches_resume_result():
    order: list[str] = []
    runtime_hook_registry.register(
        RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
        PatchCurrentUpdateHook("after_interrupt_resolve", order, "resolve_hook_seen"),
    )

    async def _run():
        return await router_node(
            {
                "task_pool": [_pending_intervention_task()],
                "route_count": 0,
                "messages": [
                    HumanMessage(content="[intervention_resolved] request_id=req-1 action_key=approve")
                ],
                "run_id": "run-1",
            },
            {"configurable": {"thread_id": "thread-1"}},
        )

    result = asyncio.run(_run())

    assert order == ["after_interrupt_resolve"]
    assert result["resolve_hook_seen"] is True
    assert result["task_pool"][0]["resolve_hook_seen"] is True
    assert result["task_pool"][0]["status"] == "RUNNING"


def test_gateway_resolve_runs_lifecycle_and_state_commit_hooks():
    order: list[str] = []
    runtime_hook_registry.register(
        RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
        PatchCurrentUpdateHook("after_interrupt_resolve", order, "resolve_hook_seen"),
    )
    runtime_hook_registry.register(
        RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
        PatchCurrentUpdateHook("before_task_pool_commit", order, "commit_hook_seen"),
    )

    task = _pending_intervention_task()
    client_mock = _mock_langgraph_client(state_values={"task_pool": [task], "run_id": "run-1"})
    fake_sdk = SimpleNamespace(get_client=lambda url: client_mock)

    with patch.dict("sys.modules", {"langgraph_sdk": fake_sdk}):
        with TestClient(_make_app()) as client:
            response = client.post(
                "/api/threads/thread-1/interventions/req-1:resolve",
                json={
                    "fingerprint": "fp-1",
                    "action_key": "approve",
                    "payload": {"comment": "go ahead"},
                },
            )

    assert response.status_code == 200
    updated_values = client_mock.threads.update_state.await_args.kwargs["values"]
    assert order == ["after_interrupt_resolve", "before_task_pool_commit"]
    assert updated_values["resolve_hook_seen"] is True
    assert updated_values["commit_hook_seen"] is True
    assert updated_values["task_pool"][0]["resolve_hook_seen"] is True
    assert updated_values["task_pool"][0]["commit_hook_seen"] is True


def test_traced_node_runs_state_commit_hooks_after_after_node_hooks():
    order: list[str] = []
    runtime_hook_registry.register(
        RuntimeHookName.AFTER_EXECUTOR,
        PatchCurrentUpdateHook("after_executor", order, "after_node_seen"),
    )
    runtime_hook_registry.register(
        RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
        PatchCurrentUpdateHook("before_task_pool_commit", order, "task_pool_commit_seen"),
    )
    runtime_hook_registry.register(
        RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT,
        PatchCurrentUpdateHook("before_verified_facts_commit", order, "verified_facts_commit_seen"),
    )

    async def node(_state, _config):
        return {
            "execution_state": "EXECUTING_DONE",
            "task_pool": [{"task_id": "task-1", "status": "DONE"}],
            "verified_facts": {"task-1": {"summary": "done"}},
        }

    wrapped = traced_node("executor")(node)
    result = asyncio.run(wrapped({"run_id": "run-1", "task_pool": []}, {"configurable": {"thread_id": "thread-1"}}))

    assert order == ["after_executor", "before_task_pool_commit", "before_verified_facts_commit"]
    assert result["after_node_seen"] is True
    assert result["task_pool_commit_seen"] is True
    assert result["verified_facts_commit_seen"] is True
    assert result["task_pool"][0]["verified_facts_commit_seen"] is True
