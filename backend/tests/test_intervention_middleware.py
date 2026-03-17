import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from src.agents.middlewares.intervention_middleware import (
    InterventionMiddleware,
    _generate_fingerprint,
)


def _tool_request(name: str, args: dict, tool_call_id: str = "call-1"):
    return SimpleNamespace(tool_call={"id": tool_call_id, "name": name, "args": args})


def test_intervention_middleware_prioritizes_explicit_policy_over_parser():
    middleware = InterventionMiddleware(
        intervention_policies={
            "update_ticket": {
                "requires_intervention": True,
                "title": "需要审批",
                "reason": "该操作会修改工单",
            }
        },
        run_id="run-1",
        task_id="task-1",
        agent_name="ops-agent",
    )
    request = _tool_request("update_ticket", {"ticket_id": "INC-1"})

    result = middleware.wrap_tool_call(request, lambda _request: ToolMessage(content="ok", tool_call_id="call-1"))

    assert isinstance(result, Command)
    assert result.goto == END
    payload = json.loads(result.update["messages"][0].content)
    assert payload["title"] == "需要审批"
    assert payload["reason"] == "该操作会修改工单"
    assert payload["tool_name"] == "update_ticket"


def test_intervention_middleware_uses_parser_when_no_policy_match():
    middleware = InterventionMiddleware(
        run_id="run-1",
        task_id="task-1",
        agent_name="ops-agent",
    )
    request = _tool_request("send_email", {"to": "user@example.com"})

    result = middleware.wrap_tool_call(request, lambda _request: ToolMessage(content="ok", tool_call_id="call-1"))

    assert isinstance(result, Command)
    payload = json.loads(result.update["messages"][0].content)
    assert payload["intervention_type"] == "before_tool"
    assert payload["tool_name"] == "send_email"
    assert payload["action_schema"]["actions"][0]["key"] == "approve"


def test_intervention_middleware_falls_back_to_hitl_keywords():
    middleware = InterventionMiddleware(
        hitl_keywords=["archive"],
        run_id="run-1",
        task_id="task-1",
        agent_name="ops-agent",
    )
    request = _tool_request("archive_record", {"record_id": "42"})

    result = middleware.wrap_tool_call(request, lambda _request: ToolMessage(content="ok", tool_call_id="call-1"))

    assert isinstance(result, Command)
    payload = json.loads(result.update["messages"][0].content)
    assert payload["tool_name"] == "archive_record"
    assert payload["fingerprint"]


def test_intervention_middleware_skips_same_fingerprint_already_resolved():
    tool_args = {"ticket_id": "INC-1"}
    fingerprint = _generate_fingerprint("run-1", "task-1", "ops-agent", "update_ticket", tool_args)
    middleware = InterventionMiddleware(
        run_id="run-1",
        task_id="task-1",
        agent_name="ops-agent",
        resolved_fingerprints={fingerprint},
        intervention_policies={
            "update_ticket": {"requires_intervention": True}
        },
    )
    request = _tool_request("update_ticket", tool_args)

    handler_calls = {"count": 0}

    def _handler(_request):
        handler_calls["count"] += 1
        return ToolMessage(content="executed", tool_call_id="call-1")

    result = middleware.wrap_tool_call(request, _handler)

    assert isinstance(result, ToolMessage)
    assert result.content == "executed"
    assert handler_calls["count"] == 1


def test_intervention_middleware_does_not_block_read_only_tool_without_policy():
    middleware = InterventionMiddleware(
        hitl_keywords=["send"],
        run_id="run-1",
        task_id="task-1",
        agent_name="ops-agent",
    )
    request = _tool_request("get_send_status", {"message_id": "m-1"})

    handler_calls = {"count": 0}

    def _handler(_request):
        handler_calls["count"] += 1
        return ToolMessage(content="read-only result", tool_call_id="call-1")

    result = middleware.wrap_tool_call(request, _handler)

    assert isinstance(result, ToolMessage)
    assert result.content == "read-only result"
    assert handler_calls["count"] == 1
