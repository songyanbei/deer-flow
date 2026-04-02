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
from typing import Any, AsyncIterator, Callable

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

LANGGRAPH_URL = "http://127.0.0.1:2024"
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


# ── Helpers ───────────────────────────────────────────────────────────


def _get_client():
    """Return a LangGraph SDK client pointing at the local server."""
    return get_client(url=LANGGRAPH_URL)


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


async def stream_message(
    *,
    thread_id: str,
    message: str,
    context: dict[str, Any],
    on_submit_success: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """Submit a message to the LangGraph runtime and yield normalized SSE frames.

    *context* is injected as the ``context`` parameter on ``runs.stream``.
    *on_submit_success* is invoked exactly once after the upstream run is
    successfully created (i.e. the first chunk is received from ``runs.stream``).
    This allows callers to persist metadata only after a successful submission.
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

    run_id: str | None = None
    # Track state for deduplication across multiple `values` snapshots
    _last_ai_content: str | None = None
    _last_artifacts_count: int = 0
    _intervention_emitted: bool = False
    _submit_callback_fired: bool = False

    # Yield initial ack
    yield _format_sse(SSE_ACK, {"thread_id": thread_id})

    try:
        async for chunk in client.runs.stream(
            thread_id,
            ENTRY_GRAPH_ASSISTANT_ID,
            input=input_payload,
            config={"recursion_limit": 1000},
            context=context,
            stream_mode=["values", "messages"],
            multitask_strategy="reject",
        ):
            # Fire the submit-success callback once after the first upstream chunk
            if not _submit_callback_fired:
                _submit_callback_fired = True
                if on_submit_success is not None:
                    on_submit_success()

            events = _normalize_stream_event(
                chunk, thread_id, run_id,
                _last_ai_content=_last_ai_content,
                _last_artifacts_count=_last_artifacts_count,
                _intervention_emitted=_intervention_emitted,
            )
            for event_name, event_data in events:
                if event_data and event_data.get("run_id"):
                    run_id = event_data["run_id"]
                # Update dedup trackers
                if event_name == SSE_MESSAGE_COMPLETED:
                    _last_ai_content = event_data.get("content")
                if event_name == SSE_ARTIFACT_CREATED:
                    _last_artifacts_count = event_data.get("_artifacts_count", _last_artifacts_count)
                    event_data.pop("_artifacts_count", None)
                if event_name == SSE_INTERVENTION_REQUESTED:
                    _intervention_emitted = True
                yield _format_sse(event_name, event_data)

        # Final run_completed after stream ends normally
        yield _format_sse(SSE_RUN_COMPLETED, {"thread_id": thread_id, "run_id": run_id})

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
    _intervention_emitted: bool = False,
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
        return _handle_values_event(data, base, _last_ai_content, _last_artifacts_count, _intervention_emitted)

    # langgraph_sdk stream_mode="messages" yields events like:
    #   "messages/partial" (streaming chunks) and "messages/complete" (finished messages)
    if event.startswith("messages"):
        return _handle_messages_event(event, data, base)

    # Unknown event type — skip
    return []


def _handle_values_event(
    data: Any,
    base: dict[str, Any],
    last_ai_content: str | None,
    last_artifacts_count: int,
    intervention_emitted: bool,
) -> list[tuple[str, dict[str, Any]]]:
    """Extract stable events from a full state snapshot."""
    if not isinstance(data, dict):
        return []

    results: list[tuple[str, dict[str, Any]]] = []

    # Check for new AI message (deduplicate against last seen content)
    messages = data.get("messages") or []
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, dict):
            content = last_msg.get("content", "")
            msg_type = last_msg.get("type", "")
            if msg_type == "ai" and content and content != last_ai_content:
                results.append((SSE_MESSAGE_COMPLETED, {**base, "content": content}))

    # Check for new artifacts (only emit if count increased)
    artifacts = data.get("artifacts") or []
    current_count = len(artifacts) if isinstance(artifacts, list) else 0
    if current_count > last_artifacts_count and artifacts:
        last_artifact = artifacts[-1]
        if isinstance(last_artifact, dict):
            artifact_payload = {
                **base,
                "artifact": last_artifact,
                "_artifacts_count": current_count,
            }
            artifact_url = _extract_artifact_url(last_artifact)
            if artifact_url:
                artifact_payload["artifact_url"] = artifact_url
            results.append((SSE_ARTIFACT_CREATED, artifact_payload))

    # Check for pending intervention (state-based, not string-matching)
    task_pool = data.get("task_pool") or []
    has_pending_intervention = any(
        isinstance(t, dict)
        and t.get("status") == "WAITING_INTERVENTION"
        and t.get("intervention_status") == "pending"
        for t in task_pool
    )
    if has_pending_intervention and not intervention_emitted:
        # Find the pending task to include details
        pending_task = next(
            (t for t in reversed(task_pool)
             if isinstance(t, dict)
             and t.get("status") == "WAITING_INTERVENTION"
             and t.get("intervention_status") == "pending"),
            None,
        )
        intervention_payload = {**base}
        if pending_task:
            intv_req = pending_task.get("intervention_request")
            if isinstance(intv_req, dict):
                intervention_payload["request_id"] = intv_req.get("request_id")
                intervention_payload["intervention_type"] = intv_req.get("type")
                fingerprint = intv_req.get("fingerprint")
                if isinstance(fingerprint, str) and fingerprint.strip():
                    intervention_payload["fingerprint"] = fingerprint
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
