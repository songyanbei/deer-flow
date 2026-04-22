"""LangGraph runtime adapter for the external platform integration.

Responsibilities:
1. Create upstream LangGraph threads
2. Fetch thread state summary
3. Submit one message to upstream runtime and stream normalized SSE events
4. Convert upstream exceptions to Gateway HTTP/SSE errors
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Callable, NoReturn

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://127.0.0.1:2024")
ENTRY_GRAPH_ASSISTANT_ID = "entry_graph"

# ── Stable SSE event names exposed to the external platform ───────────


SSE_ACK = "ack"
SSE_MESSAGE_DELTA = "message_delta"
SSE_MESSAGE_COMPLETED = "message_completed"
SSE_ARTIFACT_CREATED = "artifact_created"
SSE_INTERVENTION_REQUESTED = "intervention_requested"
SSE_GOVERNANCE_CREATED = "governance_created"
SSE_RUN_COMPLETED = "run_completed"
SSE_RUN_FAILED = "run_failed"

# Phase 1 D1.3 additions — projections the main chat UI needs to drive its
# `ThreadTitle`, `TodoList`, `task-panel`, `WorkflowFooterBar`, and
# `OrchestrationSummary` surfaces off the Gateway SSE alone. See
# ``collaboration/handoffs/frontend-to-backend.md`` §"Gateway SSE event
# parity for main chat (Phase 1 D1.2 blocker)".
SSE_STATE_SNAPSHOT = "state_snapshot"


# Fields lifted from each ``values`` chunk into the state_snapshot event.
# This preserves LangGraph shapes so the frontend's existing reducers
# (``mergeThreadValuesWithPatch``, task-context store, etc.) can apply the
# payload 1:1 without a translation layer.
_STATE_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "title",
    "todos",
    "task_pool",
    "workflow_stage",
    "workflow_stage_detail",
    "workflow_stage_updated_at",
    "resolved_orchestration_mode",
    "orchestration_reason",
)


# Custom-channel event types projected from upstream ``get_stream_writer()``
# payloads (stream_mode="custom"). These are pass-through — the Gateway keeps
# the writer-chosen ``type`` as the SSE event name so the frontend's existing
# task / workflow handlers can stay untouched. Only payloads whose ``type``
# is in this allow-list are projected; unknown types are dropped to keep the
# SSE contract stable and prevent agent-internal debug payloads from leaking.
_ALLOWED_CUSTOM_EVENT_TYPES: frozenset[str] = frozenset(
    {
        # Subagent task lifecycle (task_tool.py + multi-agent extensions)
        "task_started",
        "task_running",
        "task_waiting_intervention",
        "task_waiting_dependency",
        "task_help_requested",
        "task_resumed",
        "task_completed",
        "task_failed",
        "task_timed_out",
        # Workflow stage transitions (planner/node.py)
        "workflow_stage_changed",
    }
)


# ── Helpers ───────────────────────────────────────────────────────────


_cached_client = None


def _get_client():
    """Return a cached LangGraph SDK client pointing at the local server.

    The client is created once and reused across requests to avoid creating
    a new HTTP connection for every call.
    """
    global _cached_client
    if _cached_client is None:
        _cached_client = get_client(url=LANGGRAPH_URL)
    return _cached_client


class RuntimeServiceError(Exception):
    """Base exception for runtime service failures."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Thread creation ───────────────────────────────────────────────────


async def create_thread() -> dict[str, Any]:
    """Create a new LangGraph thread.

    Returns the raw thread dict from the SDK (contains ``thread_id`` etc.).
    Raises ``RuntimeServiceError`` on upstream failure.
    """
    try:
        client = _get_client()
        thread = await client.threads.create()
        return thread
    except Exception as exc:
        logger.error("[RuntimeService] Failed to create LangGraph thread: %s", exc)
        raise RuntimeServiceError(f"LangGraph thread creation failed: {exc}") from exc


# ── Thread state summary ──────────────────────────────────────────────


_EMPTY_STATE_SUMMARY = {
    "title": None,
    "run_id": None,
    "workflow_stage": None,
    "workflow_stage_detail": None,
    "artifacts_count": 0,
    "pending_intervention": False,
}


async def get_thread_state_summary(thread_id: str) -> dict[str, Any]:
    """Fetch a summary of the current LangGraph thread state.

    Returns a dict with normalized fields:
    - title, run_id, workflow_stage, workflow_stage_detail
    - artifacts_count, pending_intervention

    Raises ``RuntimeServiceError`` with appropriate status codes:
    - 404 when thread does not exist upstream
    - 503 when LangGraph is unreachable
    """
    try:
        client = _get_client()
        thread_state = await client.threads.get_state(thread_id)
        values = (
            thread_state.get("values", {})
            if isinstance(thread_state, dict)
            else getattr(thread_state, "values", {})
        )
    except Exception as exc:
        exc_text = str(exc).lower()
        # Distinguish "not found" from connectivity errors.
        # langgraph_sdk raises HTTPStatusError for 404, various connection
        # errors for unreachable server.
        if "404" in exc_text or "not found" in exc_text:
            raise RuntimeServiceError(f"Thread not found upstream: {thread_id}", status_code=404) from exc
        logger.error("[RuntimeService] Failed to get thread state for '%s': %s", thread_id, exc)
        raise RuntimeServiceError(f"LangGraph unavailable: {exc}", status_code=503) from exc

    # Thread exists but has no runs yet → values may be empty / None
    if not values:
        return dict(_EMPTY_STATE_SUMMARY)

    # Extract run_id from metadata if available
    metadata = (
        thread_state.get("metadata", {})
        if isinstance(thread_state, dict)
        else getattr(thread_state, "metadata", {})
    )
    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None

    # Pending intervention check
    task_pool = values.get("task_pool") or []
    pending_intervention = any(
        isinstance(t, dict)
        and t.get("status") == "WAITING_INTERVENTION"
        and t.get("intervention_status") == "pending"
        for t in task_pool
    )

    artifacts = values.get("artifacts") or []

    return {
        "title": values.get("title"),
        "run_id": run_id,
        "workflow_stage": values.get("workflow_stage"),
        "workflow_stage_detail": values.get("workflow_stage_detail"),
        "artifacts_count": len(artifacts) if isinstance(artifacts, list) else 0,
        "pending_intervention": pending_intervention,
    }


# ── Message streaming ─────────────────────────────────────────────────


def _format_sse(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_artifact_url(artifact: dict[str, Any]) -> str | None:
    """Return a stable top-level artifact URL when one is present."""
    for key in ("artifact_url", "url", "download_url", "file_url"):
        value = artifact.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _sanitize_error(exc: Exception) -> str:
    """Map internal upstream errors to stable external error text."""
    exc_text = str(exc).lower()
    if any(token in exc_text for token in ("404", "not found")):
        return "Runtime thread not found"
    if any(
        token in exc_text
        for token in (
            "connection refused",
            "connecterror",
            "connection reset",
            "connection aborted",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "service unavailable",
            "unreachable",
        )
    ):
        return "Upstream runtime unavailable"
    if any(token in exc_text for token in ("reject", "multitask", "already running", "409")):
        return "Runtime rejected the submission"
    return "Runtime execution failed"


async def submit_and_stream(
    *,
    thread_id: str,
    message: str,
    context: dict[str, Any],
    on_submit_success: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """Submit a message to the LangGraph runtime and yield normalized SSE frames.

    **Two-phase design** — the caller must ``await`` the coroutine returned by
    :func:`start_stream` first.  That coroutine initiates the upstream
    ``runs.stream`` call and waits for the first chunk.  If the upstream
    rejects the submission (connection refused, 404, 409, …) it raises
    ``RuntimeServiceError`` **before** any HTTP response is sent, so the
    router can return a proper HTTP 503/404/409 instead of a 200 SSE stream
    that immediately contains ``run_failed``.

    After ``start_stream`` succeeds the caller wraps :func:`iter_events` in a
    ``StreamingResponse``.

    *context* is injected as the ``context`` parameter on ``runs.stream``.
    *on_submit_success* is invoked exactly once after the first upstream chunk
    is received, allowing the caller to persist metadata only after successful
    submission.
    """
    # This is a convenience wrapper kept for backward-compat with tests that
    # call ``stream_message`` directly.  Production code should use the
    # two-phase ``start_stream`` / ``iter_events`` pair via the router.
    first_chunk, upstream_iter = await start_stream(
        thread_id=thread_id,
        message=message,
        context=context,
    )
    if on_submit_success is not None:
        on_submit_success()
    async for frame in iter_events(
        thread_id=thread_id,
        first_chunk=first_chunk,
        upstream_iter=upstream_iter,
    ):
        yield frame


# Keep the old name as an alias so existing callers / tests don't break.
stream_message = submit_and_stream


async def start_stream(
    *,
    thread_id: str,
    message: str,
    context: dict[str, Any],
) -> tuple[Any, Any]:
    """Initiate the upstream LangGraph run and return the first chunk + iterator.

    Raises ``RuntimeServiceError`` if the upstream rejects immediately
    (connection refused → 503, thread not found → 404, already running → 409).
    This must be awaited **before** the HTTP response is committed so the
    router can map the error to an appropriate HTTP status code.
    """
    client = _get_client()

    input_payload = {
        "messages": [
            {
                "type": "human",
                "content": [{"type": "text", "text": message}],
            }
        ],
    }

    # LG1.x (langgraph-api >= 0.7.65) rejects submits that carry both
    # ``config.configurable`` and ``context`` at the same time with HTTP 400.
    # Identity (`thread_id`, `tenant_id`, `user_id`, `thread_context`,
    # `auth_user`) is already present in ``context`` (built by the router),
    # and the remote LangGraph API mirrors it back into ``configurable`` so
    # ``ThreadDataMiddleware`` still reads it from its authoritative source.
    # Therefore we send ``config`` with recursion limit only and rely on
    # ``context`` as the single identity channel for remote submits.
    run_config: dict = {"recursion_limit": 1000}

    try:
        upstream_iter = client.runs.stream(
            thread_id,
            ENTRY_GRAPH_ASSISTANT_ID,
            input=input_payload,
            config=run_config,
            context=dict(context),
            stream_mode=["values", "messages", "custom"],
            multitask_strategy="reject",
        )
        upstream_aiter = upstream_iter.__aiter__()
        first_chunk = await upstream_aiter.__anext__()
        return first_chunk, upstream_aiter
    except StopAsyncIteration:
        return None, None
    except Exception as exc:
        _raise_runtime_error(exc, thread_id)


async def start_resume_stream(
    *,
    thread_id: str,
    context: dict[str, Any],
    message: str | None = None,
    checkpoint: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    """Initiate an upstream LangGraph resume run and return the first chunk + iterator.

    Mirrors :func:`start_stream` but supports intervention/interrupt resume
    semantics:

    - When ``checkpoint`` is provided, the upstream resumes from that
      checkpoint rather than appending a fresh message at the head of the
      current checkpoint chain. This lets the Gateway resume an interrupted
      workflow run instead of starting a new one.
    - When ``command`` is provided (``Command.resume`` / ``Command.goto``),
      the resume payload is delivered to the ``interrupt()`` caller inside
      the graph. ``command`` and ``message`` may both be supplied — the
      ``message`` becomes the next human-turn input while ``command``
      carries the resume value for the pending interrupt.

    Identity (``thread_context`` / ``auth_user`` / tenant / user / thread)
    is expected to already be in ``context`` — the router builds it from
    auth middleware + ``resolve_thread_context``. Single-channel remote
    submit is preserved (LG1.x rejects dual ``configurable + context``).
    """
    client = _get_client()

    # Resume preserves the legacy ``thread.submit({ streamResumable: true,
    # streamMode: ["values", "messages-tuple", "custom"] })`` contract used by
    # the browser InterventionCard path. ``stream_resumable=True`` lets the
    # frontend reconnect mid-resume without dropping events; ``messages-tuple``
    # yields ``(message, metadata)`` tuples so the normalizer can pull run_id
    # from metadata on every chunk.
    kwargs: dict[str, Any] = {
        "config": {"recursion_limit": 1000},
        "context": dict(context),
        "stream_mode": ["values", "messages-tuple", "custom"],
        "stream_resumable": True,
        "multitask_strategy": "reject",
    }
    if message is not None:
        kwargs["input"] = {
            "messages": [
                {
                    "type": "human",
                    "content": [{"type": "text", "text": message}],
                }
            ],
        }
    else:
        # Pure Command-based resume — upstream accepts ``input=None``.
        kwargs["input"] = None
    if checkpoint is not None:
        kwargs["checkpoint"] = checkpoint
    if command is not None:
        kwargs["command"] = command

    try:
        upstream_iter = client.runs.stream(
            thread_id,
            ENTRY_GRAPH_ASSISTANT_ID,
            **kwargs,
        )
        upstream_aiter = upstream_iter.__aiter__()
        first_chunk = await upstream_aiter.__anext__()
        return first_chunk, upstream_aiter
    except StopAsyncIteration:
        return None, None
    except Exception as exc:
        _raise_runtime_error(exc, thread_id)


def _raise_runtime_error(exc: Exception, thread_id: str) -> NoReturn:
    """Map upstream SDK exceptions to ``RuntimeServiceError`` with HTTP status."""
    exc_text = str(exc).lower()
    if "404" in exc_text or "not found" in exc_text:
        raise RuntimeServiceError(f"Runtime thread not found: {thread_id}", status_code=404) from exc
    if any(tok in exc_text for tok in ("reject", "multitask", "already running", "409")):
        raise RuntimeServiceError("Runtime rejected the submission (already running)", status_code=409) from exc
    raise RuntimeServiceError(f"LangGraph submission failed: {_sanitize_error(exc)}", status_code=503) from exc


async def iter_events(
    *,
    thread_id: str,
    first_chunk: Any,
    upstream_iter: Any,
) -> AsyncIterator[str]:
    """Yield normalized SSE frames from an already-started upstream stream.

    *first_chunk* is the chunk obtained by ``start_stream``.
    *upstream_iter* is the remaining async iterator.
    """
    run_id: str | None = None
    _last_ai_content: str | None = None
    _last_artifacts_count: int = 0
    _emitted_intervention_ids: set[str] = set()
    # D1.3 state_snapshot dedupe — holds the last projected snapshot dict so
    # identical consecutive chunks don't produce redundant SSE frames.
    _last_snapshot: dict[str, Any] = {}
    # Terminal state captured from the final values chunk, used to enrich the
    # ``run_completed`` payload so the frontend's ``onFinish`` equivalent can
    # trigger desktop notifications and query invalidation without a separate
    # ``GET /threads/{id}`` round-trip.
    _final_snapshot: dict[str, Any] = {}

    yield _format_sse(SSE_ACK, {"thread_id": thread_id})

    def _process_chunk(chunk: Any) -> list[str]:
        nonlocal run_id, _last_ai_content, _last_artifacts_count, _last_snapshot, _final_snapshot
        frames: list[str] = []
        events = _normalize_stream_event(
            chunk, thread_id, run_id,
            _last_ai_content=_last_ai_content,
            _last_artifacts_count=_last_artifacts_count,
            _emitted_intervention_ids=_emitted_intervention_ids,
            _last_snapshot=_last_snapshot,
        )
        for event_name, event_data in events:
            if event_data and event_data.get("run_id"):
                run_id = event_data["run_id"]
            if event_name == SSE_MESSAGE_COMPLETED:
                _last_ai_content = event_data.get("content")
            if event_name == SSE_ARTIFACT_CREATED:
                _last_artifacts_count = event_data.get("_artifacts_count", _last_artifacts_count)
                event_data.pop("_artifacts_count", None)
            if event_name == SSE_STATE_SNAPSHOT:
                # Refresh the dedupe baseline AND stash the latest snapshot
                # for the terminal run_completed payload.
                _last_snapshot = {k: event_data.get(k) for k in _STATE_SNAPSHOT_FIELDS if k in event_data}
                _final_snapshot = dict(_last_snapshot)
                if "artifacts_count" in event_data:
                    _final_snapshot["artifacts_count"] = event_data["artifacts_count"]
                if "messages_count" in event_data:
                    _final_snapshot["messages_count"] = event_data["messages_count"]
            frames.append(_format_sse(event_name, event_data))
        return frames

    try:
        # Process the first chunk (already fetched by start_stream)
        if first_chunk is not None:
            for frame in _process_chunk(first_chunk):
                yield frame

        # Continue with the rest of the stream
        if upstream_iter is not None:
            async for chunk in upstream_iter:
                for frame in _process_chunk(chunk):
                    yield frame

        completed_payload: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
        if _final_snapshot:
            completed_payload["final_state"] = _final_snapshot
        if _last_ai_content:
            completed_payload["last_ai_content"] = _last_ai_content
        yield _format_sse(SSE_RUN_COMPLETED, completed_payload)

    except Exception as exc:
        logger.error("[RuntimeService] Stream error for thread '%s': %s", thread_id, exc)
        yield _format_sse(SSE_RUN_FAILED, {
            "thread_id": thread_id,
            "run_id": run_id,
            "error": _sanitize_error(exc),
        })


def _normalize_stream_event(
    chunk: Any,
    thread_id: str,
    current_run_id: str | None,
    *,
    _last_ai_content: str | None = None,
    _last_artifacts_count: int = 0,
    _emitted_intervention_ids: set[str] | None = None,
    _last_snapshot: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Map a raw LangGraph stream chunk to a list of stable (event_name, payload) pairs.

    Returns an empty list for events that should be silently skipped.
    """
    # The langgraph_sdk stream yields StreamPart objects with .event and .data
    event = getattr(chunk, "event", None) or (chunk[0] if isinstance(chunk, (list, tuple)) else None)
    data = getattr(chunk, "data", None) or (chunk[1] if isinstance(chunk, (list, tuple)) and len(chunk) > 1 else None)

    if event is None or data is None:
        return []

    base = {"thread_id": thread_id, "run_id": current_run_id}

    if event == "values":
        return _handle_values_event(
            data,
            base,
            _last_ai_content,
            _last_artifacts_count,
            _emitted_intervention_ids,
            _last_snapshot,
        )

    # langgraph_sdk stream_mode="messages" yields events like:
    #   "messages/partial" (streaming chunks) and "messages/complete" (finished messages)
    if event.startswith("messages"):
        return _handle_messages_event(event, data, base)

    # langgraph_sdk stream_mode="custom" yields writer payloads dispatched via
    # ``get_stream_writer()`` from task_tool / planner / router / executor.
    if event == "custom":
        return _handle_custom_event(data, base)

    # Unknown event type — skip
    return []


def _handle_values_event(
    data: Any,
    base: dict[str, Any],
    last_ai_content: str | None,
    last_artifacts_count: int,
    emitted_intervention_ids: set[str] | None = None,
    last_snapshot: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Extract stable events from a full state snapshot."""
    if not isinstance(data, dict):
        return []

    results: list[tuple[str, dict[str, Any]]] = []

    # D1.3 — project the main-chat-relevant portion of the values snapshot
    # into a ``state_snapshot`` SSE event. Only fields listed in
    # ``_STATE_SNAPSHOT_FIELDS`` are forwarded, and only when the projected
    # subset differs from the previously emitted one, so identical
    # consecutive chunks don't flood the client. Preserves LangGraph shapes.
    projected = {k: data[k] for k in _STATE_SNAPSHOT_FIELDS if k in data}
    if projected and projected != (last_snapshot or {}):
        snapshot_payload = {**base, **projected}
        # Enrich with derived scalars the frontend needs for optimistic
        # message swap and artifact counters (item 8 / item 7 of the
        # frontend-to-backend handoff).
        messages_list = data.get("messages")
        if isinstance(messages_list, list):
            snapshot_payload["messages_count"] = len(messages_list)
            # Surface the latest human message id so the frontend can swap
            # its optimistic placeholder without reading LangGraph state.
            for msg in reversed(messages_list):
                if isinstance(msg, dict) and msg.get("type") == "human":
                    msg_id = msg.get("id")
                    if msg_id:
                        snapshot_payload["last_human_message_id"] = msg_id
                    break
        artifacts_list = data.get("artifacts")
        if isinstance(artifacts_list, list):
            snapshot_payload["artifacts_count"] = len(artifacts_list)
        results.append((SSE_STATE_SNAPSHOT, snapshot_payload))

    # Check for new AI message (deduplicate against last seen content)
    messages = data.get("messages") or []
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, dict):
            content = last_msg.get("content", "")
            msg_type = last_msg.get("type", "")
            if msg_type == "ai" and content and content != last_ai_content:
                results.append((SSE_MESSAGE_COMPLETED, {**base, "content": content}))

    # Check for new artifacts — emit each newly added artifact individually.
    # The caller tracks ``_last_artifacts_count`` so we can detect which
    # artifacts are new by slicing from that offset.
    artifacts = data.get("artifacts") or []
    current_count = len(artifacts) if isinstance(artifacts, list) else 0
    if current_count > last_artifacts_count and artifacts:
        for new_artifact in artifacts[last_artifacts_count:]:
            if isinstance(new_artifact, dict):
                artifact_payload = {
                    **base,
                    "artifact": new_artifact,
                    "_artifacts_count": current_count,
                }
                artifact_url = _extract_artifact_url(new_artifact)
                if artifact_url:
                    artifact_payload["artifact_url"] = artifact_url
                results.append((SSE_ARTIFACT_CREATED, artifact_payload))

    # Check for pending interventions — emit each *new* pending intervention
    # exactly once, tracked by ``request_id`` rather than a boolean flag so
    # that multiple sequential interventions within one run are all notified.
    _seen_ids = emitted_intervention_ids if emitted_intervention_ids is not None else set()
    task_pool = data.get("task_pool") or []
    for task in task_pool:
        if not isinstance(task, dict):
            continue
        if task.get("status") != "WAITING_INTERVENTION" or task.get("intervention_status") != "pending":
            continue
        intv_req = task.get("intervention_request")
        req_id = intv_req.get("request_id") if isinstance(intv_req, dict) else None
        dedup_key = req_id or id(task)  # fallback for missing request_id
        if dedup_key in _seen_ids:
            continue
        _seen_ids.add(dedup_key)

        intervention_payload = {**base}
        if isinstance(intv_req, dict):
            intervention_payload["request_id"] = req_id
            intervention_payload["intervention_type"] = intv_req.get("intervention_type")
            fingerprint = intv_req.get("fingerprint")
            if isinstance(fingerprint, str) and fingerprint.strip():
                intervention_payload["fingerprint"] = fingerprint
            # Include fields required by the platform to render an intervention card
            for key in ("title", "reason", "source_agent", "tool_name",
                        "risk_level", "category", "action_summary",
                        "action_schema", "questions", "display"):
                val = intv_req.get(key)
                if val is not None:
                    intervention_payload[key] = val
        results.append((SSE_INTERVENTION_REQUESTED, intervention_payload))

    # Check for governance entries
    governance_queue = data.get("governance_queue") or []
    if governance_queue:
        last_gov = governance_queue[-1] if isinstance(governance_queue, list) else None
        if isinstance(last_gov, dict) and last_gov.get("status") == "pending":
            results.append((SSE_GOVERNANCE_CREATED, {
                **base,
                "governance_id": last_gov.get("id"),
            }))

    return results


def _handle_custom_event(
    data: Any,
    base: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Project a writer-dispatched custom payload to a stable SSE event.

    Writer payloads always carry a string ``type`` field naming the event
    (e.g. ``task_started``, ``workflow_stage_changed``). The Gateway keeps
    that name as the SSE event name and merges the rest of the payload with
    the standard ``{thread_id, run_id}`` base so the frontend can correlate
    the event back to its run without depending on side-channel metadata.

    Only types in ``_ALLOWED_CUSTOM_EVENT_TYPES`` are forwarded — this keeps
    the SSE contract small and prevents ad-hoc debug payloads from agent
    code leaking into the external platform stream.
    """
    if not isinstance(data, dict):
        return []

    event_type = data.get("type")
    if not isinstance(event_type, str) or event_type not in _ALLOWED_CUSTOM_EVENT_TYPES:
        return []

    # Strip the ``type`` key from the forwarded payload — it's now carried by
    # the SSE ``event:`` header. Keep every other field as-is; they're already
    # primitive JSON values produced by trusted agent code.
    projected = {k: v for k, v in data.items() if k != "type"}

    # Merge base last so per-event thread_id/run_id wins even if a writer
    # accidentally included a stale one. ``None`` run_id is intentionally kept
    # — the first few writer events may fire before the Gateway has captured
    # run_id from a values/messages chunk, and the frontend already tolerates
    # it (it backfills from later events).
    payload: dict[str, Any] = {**projected, **base}
    # Preserve writer-supplied run_id when Gateway hasn't captured one yet.
    if base.get("run_id") is None and projected.get("run_id"):
        payload["run_id"] = projected["run_id"]

    return [(event_type, payload)]


def _handle_messages_event(
    event: str,
    data: Any,
    base: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Extract stable events from a per-message incremental update."""
    # messages events carry (message_dict, metadata_dict) tuples
    if isinstance(data, (list, tuple)) and len(data) >= 1:
        msg = data[0]
    elif isinstance(data, dict):
        msg = data
    else:
        return []

    if not isinstance(msg, dict):
        return []

    msg_type = msg.get("type", "")
    content = msg.get("content", "")

    # Extract run_id from metadata if available
    metadata = data[1] if isinstance(data, (list, tuple)) and len(data) > 1 else {}
    r_id = metadata.get("run_id") if isinstance(metadata, dict) else None

    payload = {**base}
    if r_id:
        payload["run_id"] = r_id

    # "messages/partial" → streaming AI token chunks
    if msg_type == "AIMessageChunk" and content:
        payload["content"] = content
        return [(SSE_MESSAGE_DELTA, payload)]

    # "messages/complete" with type "ai" → full AI response
    if event.endswith("/complete") and msg_type == "ai" and content:
        payload["content"] = content
        return [(SSE_MESSAGE_COMPLETED, payload)]

    return []
