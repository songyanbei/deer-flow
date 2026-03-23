from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, ToolMessage

from src.agents.executor.executor import executor_node
from src.agents.planner.node import planner_node
from src.verification.base import (
    VerificationFinding,
    VerificationReport,
    VerificationResult,
    VerificationScope,
    VerificationVerdict,
)


class DummyResponse:
    def __init__(self, content):
        self.content = content


class PlannerLLMStub:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return DummyResponse(self._responses.pop(0))


class DomainAgentStub:
    def __init__(self, messages):
        self.messages = messages

    async def ainvoke(self, payload, config=None):
        return {"messages": list(self.messages)}


def _verification_result(
    verdict: VerificationVerdict,
    *,
    scope: VerificationScope,
    summary: str,
    field: str,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict,
        report=VerificationReport(
            verifier_name="test_verifier",
            scope=scope,
            verdict=verdict,
            summary=summary,
            findings=[
                VerificationFinding(
                    field=field,
                    severity="error" if verdict != VerificationVerdict.PASSED else "info",
                    message=summary,
                )
            ]
            if verdict != VerificationVerdict.PASSED
            else [],
        ),
    )


def _agent_factory(stub: DomainAgentStub):
    def _factory(_config):
        return stub

    return _factory


def _base_task() -> dict:
    return {
        "task_id": "task-1",
        "description": "book meeting room",
        "assigned_agent": "meeting-agent",
        "status": "RUNNING",
        "run_id": "run-1",
    }


def _base_executor_state() -> dict:
    return {
        "messages": [HumanMessage(content="Book a meeting room for tomorrow morning.")],
        "task_pool": [_base_task()],
        "verified_facts": {},
        "run_id": "run-1",
    }


def _planner_state(**overrides) -> dict:
    state = {
        "messages": [HumanMessage(content="Check leave status and employee id.")],
        "original_input": "Check leave status and employee id.",
        "planner_goal": "Check leave status and employee id.",
        "run_id": "run-1",
        "task_pool": [],
        "verified_facts": {},
    }
    state.update(overrides)
    return state


def _patch_executor(monkeypatch, verification_result: VerificationResult, *, result_text: str = "Booked successfully."):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("src.agents.executor.executor._ensure_mcp_ready", _noop)
    monkeypatch.setattr(
        "src.agents.lead_agent.agent.make_lead_agent",
        _agent_factory(
            DomainAgentStub(
                [
                    ToolMessage(
                        name="task_complete",
                        tool_call_id="done-1",
                        content=f'{{"result_text":"{result_text}","fact_payload":{{"status":"booked"}}}}',
                    )
                ]
            )
        ),
    )
    monkeypatch.setattr(
        "src.verification.runtime.run_task_verification",
        lambda **kwargs: verification_result,
    )


def _patch_planner(monkeypatch, llm: PlannerLLMStub, verification_result: VerificationResult):
    monkeypatch.setattr("src.agents.planner.node.create_chat_model", lambda **kwargs: llm)
    monkeypatch.setattr(
        "src.agents.planner.node.list_domain_agents",
        lambda: [type("Agent", (), {"name": "meeting-agent", "description": "Books meetings"})()],
    )
    monkeypatch.setattr(
        "src.verification.runtime.run_workflow_verification",
        lambda **kwargs: verification_result,
    )


def test_executor_task_verification_pass_writes_verified_facts(monkeypatch):
    _patch_executor(
        monkeypatch,
        _verification_result(
            VerificationVerdict.PASSED,
            scope=VerificationScope.TASK_RESULT,
            summary="task ok",
            field="task_result",
        ),
    )

    result = asyncio.run(executor_node(_base_executor_state(), {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "EXECUTING_DONE"
    assert result["task_pool"][0]["status"] == "DONE"
    assert result["task_pool"][0]["verification_status"] == "passed"
    assert "task-1" in result["verified_facts"]


def test_executor_task_verification_needs_replan_blocks_verified_facts(monkeypatch):
    _patch_executor(
        monkeypatch,
        _verification_result(
            VerificationVerdict.NEEDS_REPLAN,
            scope=VerificationScope.TASK_RESULT,
            summary="missing required fields",
            field="task_result",
        ),
        result_text="bad",
    )

    result = asyncio.run(executor_node(_base_executor_state(), {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "EXECUTING_DONE"
    assert result["task_pool"][0]["status"] == "FAILED"
    assert result["task_pool"][0]["verification_status"] == "needs_replan"
    assert "verified_facts" not in result
    assert result["verification_feedback"]["recommended_action"] == "replan"


def test_executor_task_verification_hard_fail_stops_run(monkeypatch):
    _patch_executor(
        monkeypatch,
        _verification_result(
            VerificationVerdict.HARD_FAIL,
            scope=VerificationScope.TASK_RESULT,
            summary="irrecoverable verification error",
            field="task_result",
        ),
    )

    result = asyncio.run(executor_node(_base_executor_state(), {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "ERROR"
    assert result["task_pool"][0]["status"] == "FAILED"
    assert result["workflow_verification_status"] == "hard_fail"
    assert "irrecoverable verification error" in result["final_result"]


def test_planner_workflow_verification_pass_reaches_done(monkeypatch):
    llm = PlannerLLMStub(['{"done": true, "summary": "Employee ID is A-1001 and no leave conflicts exist."}'])
    _patch_planner(
        monkeypatch,
        llm,
        _verification_result(
            VerificationVerdict.PASSED,
            scope=VerificationScope.WORKFLOW_RESULT,
            summary="workflow ok",
            field="final_result",
        ),
    )

    state = _planner_state(
        task_pool=[
            {"task_id": "t1", "description": "lookup id", "assigned_agent": "contacts-agent", "status": "DONE", "result": "A-1001"},
        ],
        verified_facts={"t1": {"summary": "A-1001"}},
    )
    result = asyncio.run(planner_node(state, {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "DONE"
    assert result["workflow_verification_status"] == "passed"
    assert result["verification_retry_count"] == 0
    assert result["verification_feedback"] is None


def test_planner_workflow_verification_needs_replan_queues_retry(monkeypatch):
    llm = PlannerLLMStub(['{"done": true, "summary": "Too short"}'])
    _patch_planner(
        monkeypatch,
        llm,
        _verification_result(
            VerificationVerdict.NEEDS_REPLAN,
            scope=VerificationScope.WORKFLOW_RESULT,
            summary="summary missing required details",
            field="final_result",
        ),
    )

    state = _planner_state(
        task_pool=[
            {"task_id": "t1", "description": "lookup id", "assigned_agent": "contacts-agent", "status": "DONE", "result": "A-1001"},
        ],
        verified_facts={"t1": {"summary": "A-1001"}},
    )
    result = asyncio.run(planner_node(state, {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "QUEUED"
    assert result["task_pool"] == []
    assert result["verification_retry_count"] == 1
    assert result["workflow_verification_status"] == "needs_replan"
    assert result["verification_feedback"]["source_scope"] == VerificationScope.WORKFLOW_RESULT


def test_planner_workflow_verification_hard_fail_enters_error(monkeypatch):
    llm = PlannerLLMStub(['{"done": true, "summary": "Looks fine"}'])
    _patch_planner(
        monkeypatch,
        llm,
        _verification_result(
            VerificationVerdict.HARD_FAIL,
            scope=VerificationScope.WORKFLOW_RESULT,
            summary="illegal workflow output",
            field="final_result",
        ),
    )

    state = _planner_state(
        task_pool=[{"task_id": "t1", "description": "lookup id", "assigned_agent": "contacts-agent", "status": "DONE", "result": "A-1001"}],
        verified_facts={"t1": {"summary": "A-1001"}},
    )
    result = asyncio.run(planner_node(state, {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "ERROR"
    assert result["workflow_verification_status"] == "hard_fail"
    assert result["verification_feedback"] is None


def test_planner_workflow_verification_budget_exhaustion_escalates_to_hard_fail(monkeypatch):
    llm = PlannerLLMStub(['{"done": true, "summary": "Looks fine"}'])
    _patch_planner(
        monkeypatch,
        llm,
        _verification_result(
            VerificationVerdict.NEEDS_REPLAN,
            scope=VerificationScope.WORKFLOW_RESULT,
            summary="still missing details",
            field="final_result",
        ),
    )

    state = _planner_state(
        task_pool=[{"task_id": "t1", "description": "lookup id", "assigned_agent": "contacts-agent", "status": "DONE", "result": "A-1001"}],
        verified_facts={"t1": {"summary": "A-1001"}},
        verification_retry_count=3,
        verification_feedback={"summary": "old feedback"},
    )
    result = asyncio.run(planner_node(state, {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "ERROR"
    assert result["workflow_verification_status"] == "hard_fail"
    assert result["verification_retry_count"] == 4
    assert result["verification_feedback"] is None


def test_planner_validate_prompt_includes_verification_feedback_for_replan(monkeypatch):
    llm = PlannerLLMStub(['[{"description": "retry booking with complete fields", "assigned_agent": "meeting-agent"}]'])
    _patch_planner(
        monkeypatch,
        llm,
        _verification_result(
            VerificationVerdict.PASSED,
            scope=VerificationScope.WORKFLOW_RESULT,
            summary="unused",
            field="final_result",
        ),
    )

    state = _planner_state(
        task_pool=[
            {
                "task_id": "t1",
                "description": "book meeting room",
                "assigned_agent": "meeting-agent",
                "status": "FAILED",
                "error": "Verification needs_replan: missing required fields",
            }
        ],
        verification_feedback={
            "summary": "task verification failed",
            "findings": [{"field": "task_result", "message": "result is missing attendee name"}],
            "recommended_action": "replan",
        },
    )
    result = asyncio.run(planner_node(state, {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "PLANNING_DONE"
    prompt = llm.calls[0][-1].content
    assert "task verification failed" in prompt
    assert "result is missing attendee name" in prompt
    assert "Recommended action: replan" in prompt


def test_planner_new_user_turn_clears_stale_verification_state(monkeypatch):
    llm = PlannerLLMStub([])
    monkeypatch.setattr("src.agents.planner.node.create_chat_model", lambda **kwargs: llm)
    monkeypatch.setattr(
        "src.agents.planner.node.list_domain_agents",
        lambda: [type("Agent", (), {"name": "meeting-agent", "description": "Books meetings"})()],
    )

    state = {
        "messages": [HumanMessage(content="Start a new workflow instead.")],
        "original_input": "Old request",
        "planner_goal": "Old request",
        "run_id": "run-old",
        "workflow_stage": "summarizing",
        "task_pool": [{"task_id": "t1", "description": "old", "status": "DONE"}],
        "verified_facts": {"t1": {"summary": "old"}},
        "verification_feedback": {"summary": "stale feedback"},
        "verification_retry_count": 2,
        "workflow_verification_status": "needs_replan",
        "workflow_verification_report": {"summary": "stale report"},
    }

    result = asyncio.run(planner_node(state, {"configurable": {"thread_id": "thread-1"}}))

    assert result["execution_state"] == "QUEUED"
    assert result["verification_feedback"] is None
    assert result["verification_retry_count"] == 0
    assert result["workflow_verification_status"] is None
    assert result["workflow_verification_report"] is None
