"""Phase 1 D1.3 tests — Gateway SSE event projection parity.

Covers the frontend contract listed in
``collaboration/handoffs/frontend-to-backend.md`` §"Gateway SSE event parity
for main chat (Phase 1 D1.2 blocker)":

1. Upstream ``stream_mode`` subscribes to the ``custom`` channel so
   ``get_stream_writer()`` payloads from ``task_tool`` / ``planner`` reach
   the SSE consumer.
2. Allow-listed ``custom`` payloads (``task_*``, ``workflow_stage_changed``)
   are projected 1:1 to SSE event names; unknown types are dropped.
3. Each ``values`` chunk emits a ``state_snapshot`` event carrying the
   fields main chat UI needs (title, todos, task_pool, workflow_stage*,
   orchestration_*, messages_count, artifacts_count, last_human_message_id).
4. Identical consecutive snapshots are deduped — no redundant frames.
5. Terminal ``run_completed`` payload is enriched with ``final_state`` and
   ``last_ai_content`` so the frontend's ``onFinish`` equivalent can drive
   notifications + query invalidation without an extra HTTP round-trip.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.gateway.runtime_service import (
    SSE_STATE_SNAPSHOT,
    _ALLOWED_CUSTOM_EVENT_TYPES,
    _handle_custom_event,
    _handle_values_event,
    iter_events,
    start_stream,
)


# ── Helpers ────────────────────────────────────────────────────────────


class _StubStreamPart:
    """langgraph_sdk stream yields objects with ``.event`` and ``.data``."""

    def __init__(self, event: str, data):
        self.event = event
        self.data = data


async def _collect_frames(aiter) -> list[tuple[str, dict]]:
    """Consume an SSE async iterator and return (event_name, parsed_data)."""
    frames: list[tuple[str, dict]] = []
    async for raw in aiter:
        # Each frame is "event: NAME\ndata: {...}\n\n"
        event_line, data_line, _ = raw.split("\n", 2)
        event_name = event_line.removeprefix("event: ").strip()
        data_json = data_line.removeprefix("data: ").strip()
        frames.append((event_name, json.loads(data_json)))
    return frames


class _StubRuns:
    """Minimal SDK runs stub that replays a fixed chunk sequence."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.captured_stream_mode = None

    def stream(self, thread_id, assistant_id, *, input, config, context, stream_mode, multitask_strategy):
        self.captured_stream_mode = stream_mode
        chunks = self.chunks

        async def _aiter():
            for c in chunks:
                yield c

        return _aiter()


# ── 1. Upstream stream_mode must include "custom" ─────────────────────


def test_start_stream_subscribes_custom_channel():
    """Frontend needs ``get_stream_writer()`` events in the Gateway stream."""
    stub_runs = _StubRuns(chunks=[_StubStreamPart("ack", {"thread_id": "t1"})])

    class _StubClient:
        runs = stub_runs

    async def _run():
        import src.gateway.runtime_service as rs

        rs._cached_client = _StubClient()
        try:
            await start_stream(thread_id="t1", message="hi", context={})
        finally:
            rs._cached_client = None

    asyncio.run(_run())
    assert "custom" in stub_runs.captured_stream_mode
    # Existing channels preserved
    assert "values" in stub_runs.captured_stream_mode
    assert "messages" in stub_runs.captured_stream_mode


# ── 2. Custom event projection ────────────────────────────────────────


class TestCustomEventProjection:
    def test_allowed_task_started_is_projected(self):
        events = _handle_custom_event(
            {"type": "task_started", "task_id": "tk-1", "description": "analyze"},
            base={"thread_id": "t1", "run_id": "r1"},
        )
        assert len(events) == 1
        name, payload = events[0]
        assert name == "task_started"
        assert payload["task_id"] == "tk-1"
        assert payload["description"] == "analyze"
        assert payload["thread_id"] == "t1"
        assert payload["run_id"] == "r1"
        # ``type`` must not leak into the data — it's now the SSE event name.
        assert "type" not in payload

    def test_workflow_stage_changed_is_projected(self):
        events = _handle_custom_event(
            {
                "type": "workflow_stage_changed",
                "workflow_stage": "planning",
                "workflow_stage_detail": "decomposing",
                "workflow_stage_updated_at": "2026-04-21T10:00:00Z",
            },
            base={"thread_id": "t1", "run_id": "r1"},
        )
        assert len(events) == 1
        name, payload = events[0]
        assert name == "workflow_stage_changed"
        assert payload["workflow_stage"] == "planning"
        assert payload["workflow_stage_detail"] == "decomposing"
        assert payload["workflow_stage_updated_at"] == "2026-04-21T10:00:00Z"

    def test_unknown_type_is_dropped(self):
        """Arbitrary writer payloads must not leak into the external SSE."""
        events = _handle_custom_event(
            {"type": "debug_trace", "secret": "oops"},
            base={"thread_id": "t1", "run_id": "r1"},
        )
        assert events == []

    def test_all_task_lifecycle_types_allow_listed(self):
        """Frontend ``classifyTaskEvent`` recognizes these — they must be
        projectable by the Gateway."""
        required = {
            "task_started",
            "task_running",
            "task_waiting_intervention",
            "task_waiting_dependency",
            "task_help_requested",
            "task_resumed",
            "task_completed",
            "task_failed",
            "task_timed_out",
            "workflow_stage_changed",
        }
        assert required.issubset(_ALLOWED_CUSTOM_EVENT_TYPES)

    def test_non_dict_data_is_dropped(self):
        assert _handle_custom_event("oops", base={"thread_id": "t1", "run_id": None}) == []
        assert _handle_custom_event(None, base={"thread_id": "t1", "run_id": None}) == []

    def test_missing_run_id_falls_back_to_writer_supplied(self):
        """If Gateway hasn't captured run_id yet but the writer included one,
        forward it so the frontend can correlate the event."""
        events = _handle_custom_event(
            {"type": "task_started", "task_id": "tk-1", "run_id": "r-writer"},
            base={"thread_id": "t1", "run_id": None},
        )
        _, payload = events[0]
        assert payload["run_id"] == "r-writer"


# ── 3. state_snapshot projection from values ──────────────────────────


class TestStateSnapshotProjection:
    def test_emits_snapshot_with_main_chat_fields(self):
        values = {
            "title": "My Chat",
            "todos": [{"id": "td-1", "content": "step 1", "status": "pending"}],
            "task_pool": [{"id": "tk-1", "status": "RUNNING"}],
            "workflow_stage": "executing",
            "workflow_stage_detail": "running tasks",
            "workflow_stage_updated_at": "2026-04-21T10:00:00Z",
            "resolved_orchestration_mode": "workflow",
            "orchestration_reason": "multi-step plan detected",
            "messages": [{"type": "human", "content": "hi", "id": "msg-human-1"}],
            "artifacts": [{"id": "a-1"}, {"id": "a-2"}],
        }
        events = _handle_values_event(
            values,
            base={"thread_id": "t1", "run_id": "r1"},
            last_ai_content=None,
            last_artifacts_count=0,
            emitted_intervention_ids=set(),
            last_snapshot={},
        )
        snapshot_events = [e for e in events if e[0] == SSE_STATE_SNAPSHOT]
        assert len(snapshot_events) == 1
        _, payload = snapshot_events[0]
        assert payload["title"] == "My Chat"
        assert payload["todos"] == values["todos"]
        assert payload["task_pool"] == values["task_pool"]
        assert payload["workflow_stage"] == "executing"
        assert payload["workflow_stage_detail"] == "running tasks"
        assert payload["workflow_stage_updated_at"] == "2026-04-21T10:00:00Z"
        assert payload["resolved_orchestration_mode"] == "workflow"
        assert payload["orchestration_reason"] == "multi-step plan detected"
        assert payload["messages_count"] == 1
        assert payload["last_human_message_id"] == "msg-human-1"
        assert payload["artifacts_count"] == 2
        assert payload["thread_id"] == "t1"
        assert payload["run_id"] == "r1"

    def test_identical_snapshot_is_deduped(self):
        values = {"title": "same", "workflow_stage": "planning"}
        last = {"title": "same", "workflow_stage": "planning"}
        events = _handle_values_event(
            values,
            base={"thread_id": "t1", "run_id": "r1"},
            last_ai_content=None,
            last_artifacts_count=0,
            emitted_intervention_ids=set(),
            last_snapshot=last,
        )
        assert not any(e[0] == SSE_STATE_SNAPSHOT for e in events)

    def test_changed_snapshot_re_emitted(self):
        events = _handle_values_event(
            {"title": "new title", "workflow_stage": "planning"},
            base={"thread_id": "t1", "run_id": "r1"},
            last_ai_content=None,
            last_artifacts_count=0,
            emitted_intervention_ids=set(),
            last_snapshot={"title": "old title", "workflow_stage": "planning"},
        )
        snapshots = [e for e in events if e[0] == SSE_STATE_SNAPSHOT]
        assert len(snapshots) == 1
        assert snapshots[0][1]["title"] == "new title"

    def test_absent_fields_are_omitted(self):
        """Only fields actually present in values should be forwarded."""
        events = _handle_values_event(
            {"title": "only title"},
            base={"thread_id": "t1", "run_id": "r1"},
            last_ai_content=None,
            last_artifacts_count=0,
            emitted_intervention_ids=set(),
            last_snapshot={},
        )
        snapshots = [e for e in events if e[0] == SSE_STATE_SNAPSHOT]
        assert len(snapshots) == 1
        payload = snapshots[0][1]
        assert "title" in payload
        assert "todos" not in payload
        assert "workflow_stage" not in payload

    def test_empty_values_does_not_emit_snapshot(self):
        """A values chunk with none of the tracked fields → no snapshot."""
        events = _handle_values_event(
            {"messages": []},
            base={"thread_id": "t1", "run_id": "r1"},
            last_ai_content=None,
            last_artifacts_count=0,
            emitted_intervention_ids=set(),
            last_snapshot={},
        )
        assert not any(e[0] == SSE_STATE_SNAPSHOT for e in events)


# ── 4. run_completed enrichment via iter_events ───────────────────────


class TestRunCompletedEnrichment:
    def test_run_completed_carries_final_state_and_last_ai(self):
        """After consuming a values chunk with title and an ai message, the
        terminal ``run_completed`` frame must carry ``final_state`` and
        ``last_ai_content`` so the frontend can mimic LangGraph's
        ``onFinish(state)``."""
        chunks = [
            _StubStreamPart(
                "values",
                {
                    "title": "Final Title",
                    "workflow_stage": "summarizing",
                    "messages": [
                        {"type": "human", "content": "hi", "id": "h-1"},
                        {"type": "ai", "content": "hello there"},
                    ],
                    "artifacts": [{"id": "a-1"}],
                },
            ),
        ]

        async def _fake_upstream():
            for c in chunks:
                yield c

        async def _run():
            frames = await _collect_frames(
                iter_events(thread_id="t1", first_chunk=None, upstream_iter=_fake_upstream())
            )
            return frames

        frames = asyncio.run(_run())
        names = [n for n, _ in frames]
        assert names[0] == "ack"
        assert "run_completed" in names
        completed = next(p for n, p in frames if n == "run_completed")
        assert completed["last_ai_content"] == "hello there"
        assert completed["final_state"]["title"] == "Final Title"
        assert completed["final_state"]["workflow_stage"] == "summarizing"
        assert completed["final_state"]["artifacts_count"] == 1
        assert completed["final_state"]["messages_count"] == 2

    def test_run_completed_without_values_chunk_has_no_final_state(self):
        """Backward compat: if no values chunk was seen, run_completed keeps
        its minimal shape (no ``final_state`` key)."""
        async def _empty():
            if False:
                yield  # never

        async def _run():
            return await _collect_frames(
                iter_events(thread_id="t1", first_chunk=None, upstream_iter=_empty())
            )

        frames = asyncio.run(_run())
        completed = next(p for n, p in frames if n == "run_completed")
        assert "final_state" not in completed
        assert "last_ai_content" not in completed
