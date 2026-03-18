"""Tests for executor resume optimizations: message history and intervention fast-path."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.executor.executor import (
    _deserialize_agent_messages,
    _extract_intercepted_tool_call,
    _serialize_agent_messages,
)


class TestMessageSerialization:
    """Round-trip serialization of agent conversation history."""

    def test_round_trip_simple_messages(self):
        messages = [
            HumanMessage(content="Book a room"),
            AIMessage(content="Looking up availability"),
        ]
        serialized = _serialize_agent_messages(messages)
        assert len(serialized) == 2
        restored = _deserialize_agent_messages(serialized)
        assert len(restored) == 2
        assert isinstance(restored[0], HumanMessage)
        assert isinstance(restored[1], AIMessage)
        assert restored[0].content == "Book a room"
        assert restored[1].content == "Looking up availability"

    def test_round_trip_with_tool_calls(self):
        messages = [
            HumanMessage(content="Book a room"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc1", "name": "getFreeMeetingRooms", "args": {"start": 100, "end": 200}},
                ],
            ),
            ToolMessage(content='{"rooms": ["A"]}', tool_call_id="tc1", name="getFreeMeetingRooms"),
            AIMessage(content="Found room A"),
        ]
        serialized = _serialize_agent_messages(messages)
        restored = _deserialize_agent_messages(serialized)
        assert len(restored) == 4
        assert isinstance(restored[1], AIMessage)
        assert restored[1].tool_calls[0]["name"] == "getFreeMeetingRooms"
        assert restored[1].tool_calls[0]["args"] == {"start": 100, "end": 200}
        assert isinstance(restored[2], ToolMessage)
        assert restored[2].name == "getFreeMeetingRooms"

    def test_deserialize_empty_or_none(self):
        assert _deserialize_agent_messages(None) == []
        assert _deserialize_agent_messages([]) == []

    def test_deserialize_corrupt_data_returns_empty(self):
        assert _deserialize_agent_messages([{"bad": "data"}]) == []

    def test_serialize_empty(self):
        assert _serialize_agent_messages([]) == []


class TestExtractInterceptedToolCall:
    """Extraction of the original tool call from intervention-intercepted messages."""

    def test_extracts_tool_call_before_intervention(self):
        messages = [
            HumanMessage(content="Book room"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc_create", "name": "meeting_createMeeting", "args": {"roomId": "room_1", "topic": "产品介绍"}},
                ],
            ),
            ToolMessage(
                content=json.dumps({"request_id": "intv_1", "fingerprint": "fp1"}),
                tool_call_id="tc_create",
                name="intervention_required",
            ),
        ]
        result = _extract_intercepted_tool_call(messages)
        assert result is not None
        assert result["tool_call_id"] == "tc_create"
        assert result["tool_name"] == "meeting_createMeeting"
        assert result["tool_args"] == {"roomId": "room_1", "topic": "产品介绍"}

    def test_returns_none_when_no_intervention(self):
        messages = [
            HumanMessage(content="hi"),
            AIMessage(content="hello"),
        ]
        assert _extract_intercepted_tool_call(messages) is None

    def test_returns_none_for_empty_messages(self):
        assert _extract_intercepted_tool_call([]) is None

    def test_handles_multiple_tool_calls_finds_intervention_one(self):
        messages = [
            HumanMessage(content="Book room"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc_time", "name": "time_toTimestamp", "args": {"time": "15:00"}},
                ],
            ),
            ToolMessage(content='{"timestamp": 100}', tool_call_id="tc_time", name="time_toTimestamp"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc_book", "name": "meeting_createMeeting", "args": {"roomId": "r1"}},
                ],
            ),
            ToolMessage(
                content=json.dumps({"request_id": "intv_2"}),
                tool_call_id="tc_book",
                name="intervention_required",
            ),
        ]
        result = _extract_intercepted_tool_call(messages)
        assert result is not None
        assert result["tool_name"] == "meeting_createMeeting"
        assert result["tool_call_id"] == "tc_book"
