from langchain_core.messages import AIMessage, ToolMessage

from src.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware


def test_dangling_tool_call_middleware_blocks_risky_retry_when_pending_intervention_active():
    middleware = DanglingToolCallMiddleware()
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "meeting_createMeeting", "args": {"roomId": "A"}}],
        )
    ]
    state = {
        "task_pool": [
            {
                "task_id": "task-1",
                "status": "WAITING_INTERVENTION",
                "intervention_status": "pending",
            }
        ]
    }

    patched = middleware._build_patched_messages(messages, state)

    assert patched is not None
    assert isinstance(patched[1], ToolMessage)
    assert "blocked by active pending intervention" in patched[1].content
    assert patched[1].name == "meeting_createMeeting"


def test_dangling_tool_call_middleware_keeps_generic_placeholder_for_non_risky_tool():
    middleware = DanglingToolCallMiddleware()
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "get_room_status", "args": {"roomId": "A"}}],
        )
    ]
    state = {
        "task_pool": [
            {
                "task_id": "task-1",
                "status": "WAITING_INTERVENTION",
                "intervention_status": "pending",
            }
        ]
    }

    patched = middleware._build_patched_messages(messages, state)

    assert patched is not None
    assert isinstance(patched[1], ToolMessage)
    assert patched[1].content == "[Tool call was interrupted and did not return a result.]"
