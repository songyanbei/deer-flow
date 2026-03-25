from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from src.agents.memory.queue import MemoryUpdateQueue
from src.agents.executor.executor import _build_context, executor_node
from src.agents.lead_agent.prompt import apply_prompt_template
from src.agents.persistent_domain_memory import (
    get_persistent_domain_memory_context,
    queue_persistent_domain_memory_update,
)


def test_meeting_agent_prompt_includes_runbook_for_pilot_domain():
    prompt = apply_prompt_template(agent_name="meeting-agent", is_domain_agent=True)

    assert "<runbook>" in prompt
    assert "Meeting Persistent Runbook" in prompt
    assert "Persistent Domain Memory" in prompt
    assert "<memory>" not in prompt


def test_contacts_agent_prompt_skips_persistent_runbook_by_default():
    prompt = apply_prompt_template(agent_name="contacts-agent", is_domain_agent=True)

    assert "<runbook>" not in prompt
    assert "Persistent Domain Memory" not in prompt


def test_non_pilot_domain_agents_keep_prompt_memory_injection():
    with patch("src.agents.lead_agent.prompt._get_memory_context", return_value="<memory>\nlegacy\n</memory>\n"):
        prompt = apply_prompt_template(agent_name="contacts-agent", is_domain_agent=True)

    assert "<memory>" in prompt
    assert "legacy" in prompt


def test_build_context_injects_persistent_domain_memory_only_for_pilot_domain():
    memory_data = {
        "user": {
            "topOfMind": {
                "summary": "Preferred city: Shanghai",
                "updatedAt": "2026-03-25T00:00:00Z",
            }
        }
    }

    with patch("src.agents.persistent_domain_memory.get_memory_data", return_value=memory_data):
        meeting_context = _build_context(
            {"task_id": "t1", "description": "Book meeting room", "status": "RUNNING"},
            {},
            agent_name="meeting-agent",
        )
        contacts_context = _build_context(
            {"task_id": "t2", "description": "Lookup employee", "status": "RUNNING"},
            {},
            agent_name="contacts-agent",
        )

    assert "Persistent domain memory" in meeting_context
    assert "Preferred city: Shanghai" in meeting_context
    assert "Persistent domain memory" not in contacts_context


def test_get_persistent_domain_memory_context_returns_empty_when_disabled():
    with patch("src.agents.persistent_domain_memory.get_memory_data", return_value={"facts": [{"content": "ignored"}]}):
        assert get_persistent_domain_memory_context("contacts-agent") == ""


def test_get_persistent_domain_memory_context_returns_empty_on_malformed_schema():
    with patch("src.agents.persistent_domain_memory.get_memory_data", return_value={"user": "oops"}):
        assert get_persistent_domain_memory_context("meeting-agent") == ""


def test_memory_queue_dedupes_by_logical_key_not_thread_only():
    queue = MemoryUpdateQueue()
    with patch.object(queue, "_reset_timer", return_value=None):
        queue.add(thread_id="thread-1", messages=[AIMessage(content="global")], dedupe_key="conversation:global:thread-1")
        queue.add(
            thread_id="thread-1",
            messages=[AIMessage(content="meeting-task-1")],
            agent_name="meeting-agent",
            dedupe_key="persistent-domain:meeting-agent:thread-1:task-1",
        )
        queue.add(
            thread_id="thread-1",
            messages=[AIMessage(content="meeting-task-1-updated")],
            agent_name="meeting-agent",
            dedupe_key="persistent-domain:meeting-agent:thread-1:task-1",
        )

    assert queue.pending_count == 2
    assert [ctx.agent_name for ctx in queue._queue] == [None, "meeting-agent"]
    assert queue._queue[1].messages[0].content == "meeting-task-1-updated"


def test_queue_persistent_domain_memory_update_filters_transactional_fields():
    queue = Mock()
    task = {
        "task_id": "task-1",
        "description": "Book meeting room",
        "status": "DONE",
        "run_id": "run-1",
        "resolved_inputs": {
            "contact_lookup": {"city": "Shanghai", "department": "Platform", "openId": "ou_secret"},
            "booking_request": {"room_features": ["projector", "whiteboard"]},
        },
    }
    verified_fact = {
        "summary": "Meeting booked successfully for Shanghai.",
        "payload": {
            "status": "booked",
            "city": "Shanghai",
            "meeting_id": "mtg-123",
            "openId": "ou_secret",
            "attendees": ["Alice", "Bob"],
            "booked_room": "A-301",
        },
    }

    with patch("src.agents.persistent_domain_memory.get_memory_queue", return_value=queue):
        queued = queue_persistent_domain_memory_update(
            "meeting-agent",
            task=task,
            verified_fact=verified_fact,
            thread_id="thread-1",
        )

    assert queued is True
    queue.add.assert_called_once()
    kwargs = queue.add.call_args.kwargs
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["agent_name"] == "meeting-agent"
    assert kwargs["dedupe_key"] == "persistent-domain:meeting-agent:thread-1:task-1"
    assert len(kwargs["messages"]) == 2
    assert "Preferred booking city: Shanghai" in kwargs["messages"][0].content
    assert "Organizer department hint: Platform" in kwargs["messages"][0].content
    assert "Preferred room characteristics: projector, whiteboard" in kwargs["messages"][0].content
    assert "meeting_id" not in kwargs["messages"][0].content
    assert "ou_secret" not in kwargs["messages"][0].content
    assert "Alice" not in kwargs["messages"][0].content
    assert "A-301" not in kwargs["messages"][0].content
    assert "Preferred booking city: Shanghai" in kwargs["messages"][1].content


def test_queue_persistent_domain_memory_update_skips_when_no_safe_hints():
    queue = Mock()
    task = {
        "task_id": "task-unsafe",
        "description": "Book meeting room",
        "status": "DONE",
        "run_id": "run-unsafe",
    }
    verified_fact = {
        "summary": "Meeting booked successfully.",
        "payload": {
            "status": "booked",
            "meeting_id": "mtg-unsafe",
            "openId": "ou_unsafe",
            "attendees": ["Alice", "Bob"],
        },
    }

    with patch("src.agents.persistent_domain_memory.get_memory_queue", return_value=queue):
        queued = queue_persistent_domain_memory_update(
            "meeting-agent",
            task=task,
            verified_fact=verified_fact,
            thread_id="thread-unsafe",
        )

    assert queued is False
    queue.add.assert_not_called()


def test_queue_persistent_domain_memory_update_skips_non_pilot_agent():
    queue = Mock()
    task = {
        "task_id": "task-1",
        "description": "Lookup employee",
        "status": "DONE",
        "run_id": "run-1",
    }

    with patch("src.agents.persistent_domain_memory.get_memory_queue", return_value=queue):
        queued = queue_persistent_domain_memory_update(
            "contacts-agent",
            task=task,
            verified_fact={"summary": "Employee found."},
            thread_id="thread-1",
        )

    assert queued is False
    queue.add.assert_not_called()


def test_executor_queues_persistent_memory_after_verified_meeting_success():
    class DomainStub:
        async def ainvoke(self, payload, config=None):
            return {
                "messages": [
                    AIMessage(
                        content='{"result_text":"Meeting booked successfully for Shanghai.","fact_payload":{"status":"booked","city":"Shanghai"}}'
                    )
                ]
            }

    def _make_lead_agent(_config):
        return DomainStub()

    async def _run() -> None:
        with patch("src.agents.executor.executor._ensure_mcp_ready", return_value=None):
            with patch("src.agents.executor.executor.make_lead_agent", create=True, new=_make_lead_agent):
                with patch("src.agents.lead_agent.agent.make_lead_agent", new=_make_lead_agent):
                    with patch("src.agents.executor.executor.queue_persistent_domain_memory_update") as queue_update:
                        result = await executor_node(
                            {
                                "task_pool": [
                                    {
                                        "task_id": "meeting-task-1",
                                        "description": "Book meeting room in Shanghai",
                                        "assigned_agent": "meeting-agent",
                                        "status": "RUNNING",
                                    }
                                ],
                                "verified_facts": {},
                            },
                            {"configurable": {"thread_id": "thread-stage2"}},
                        )

        assert result["execution_state"] == "EXECUTING_DONE"
        assert result["task_pool"][0]["status"] == "DONE"
        queue_update.assert_called_once()
        assert queue_update.call_args.args[0] == "meeting-agent"
        assert queue_update.call_args.kwargs["thread_id"] == "thread-stage2"

    asyncio.run(_run())
