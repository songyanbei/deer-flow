from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from importlib import import_module
from typing import Any, AsyncIterator, Mapping, Sequence
from uuid import UUID

from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.serde import json_dumpb, json_loads

from src.agents.workflow_resume import (
    looks_like_explicit_new_request,
    workflow_has_pending_clarification,
)

logger = logging.getLogger(__name__)

_PATCH_INSTALLED = False
_PATCH_SENTINEL_ATTR = "__deerflow_enqueue_time_workflow_stage_patch__"
_WORKFLOW_MODE = "workflow"
_QUEUED_DETAIL = "\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u6392\u961f\u542f\u52a8..."
_TERMINAL_STAGE_ORDER = {
    "queued": 1,
    "planning": 2,
    "routing": 3,
    "executing": 4,
    "summarizing": 5,
}


def _get_runs_ops():
    if IS_POSTGRES_OR_GRPC_BACKEND:
        return import_module("langgraph_api.grpc.ops").Runs
    try:
        return import_module("langgraph_runtime.ops").Runs
    except Exception:
        return import_module("langgraph_runtime_inmem.ops").Runs


def _get_threads_ops():
    if IS_POSTGRES_OR_GRPC_BACKEND:
        return import_module("langgraph_api.grpc.ops").Threads
    try:
        return import_module("langgraph_runtime.ops").Threads
    except Exception:
        return import_module("langgraph_runtime_inmem.ops").Threads


def _get_checkpointer_api():
    return import_module("langgraph_api._checkpointer")


def _get_thread_state_module():
    return import_module("langgraph_api.state")


def _get_checkpoint_base_module():
    return import_module("langgraph.checkpoint.base")


def _get_pregel_utils_module():
    return import_module("langgraph.pregel._utils")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetchone(
    iterator: AsyncIterator[Any],
    *,
    not_found_detail: str,
) -> Any:
    try:
        return await anext(iterator)
    except StopAsyncIteration:
        raise LookupError(not_found_detail) from None


def _stringify_uuid(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    text = str(value).strip()
    return text or None


def _normalize_requested_mode(value: object) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"auto", "leader", _WORKFLOW_MODE}:
            return lowered
    return "auto"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        if "text" in content:
            return str(content.get("text") or "")
        if "content" in content:
            return _content_to_text(content.get("content"))
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            text = _content_to_text(part).strip()
            if text:
                parts.append(text)
        return " ".join(parts)
    return str(content or "")


def _is_human_like_message(message: object) -> bool:
    if isinstance(message, Mapping):
        role = str(message.get("role") or message.get("type") or "").lower()
        return role in {"user", "human"}
    role = str(getattr(message, "role", "") or getattr(message, "type", "")).lower()
    return role in {"user", "human"}


def _extract_latest_human_input(messages: object) -> str | None:
    if isinstance(messages, Mapping):
        if _is_human_like_message(messages):
            text = _content_to_text(messages.get("content"))
            return text.strip() or None
        return None
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        return None
    for message in reversed(messages):
        if _is_human_like_message(message):
            if isinstance(message, Mapping):
                text = _content_to_text(message.get("content"))
            else:
                text = _content_to_text(getattr(message, "content", ""))
            normalized = text.strip()
            if normalized:
                return normalized
    return None


def _requested_mode_from_run(run: Mapping[str, Any]) -> str:
    kwargs = run.get("kwargs") if isinstance(run, Mapping) else None
    if not isinstance(kwargs, Mapping):
        return "auto"

    config = kwargs.get("config")
    context = kwargs.get("context")

    configurable: Mapping[str, Any] | None = None
    if isinstance(config, Mapping):
        raw = config.get("configurable")
        if isinstance(raw, Mapping):
            configurable = raw
    if configurable is None and isinstance(context, Mapping):
        configurable = context

    if configurable is None:
        return "auto"

    return _normalize_requested_mode(
        configurable.get("requested_orchestration_mode")
        or configurable.get("orchestration_mode")
    )


def _run_resume_metadata(run: Mapping[str, Any]) -> tuple[bool, str | None, str | None]:
    kwargs = run.get("kwargs") if isinstance(run, Mapping) else None
    if not isinstance(kwargs, Mapping):
        return False, None, None

    config = kwargs.get("config")
    context = kwargs.get("context")

    configurable: Mapping[str, Any] | None = None
    if isinstance(config, Mapping):
        raw = config.get("configurable")
        if isinstance(raw, Mapping):
            configurable = raw

    resume_payload = configurable if configurable is not None else None
    if resume_payload is None and isinstance(context, Mapping):
        resume_payload = context

    if not isinstance(resume_payload, Mapping):
        return False, None, None

    resume_flag = bool(resume_payload.get("workflow_clarification_resume"))
    resume_run_id = _stringify_uuid(resume_payload.get("workflow_resume_run_id"))
    resume_task_id = _stringify_uuid(resume_payload.get("workflow_resume_task_id"))
    return resume_flag, resume_run_id, resume_task_id


def _coerce_thread_values(raw_values: object) -> dict[str, Any]:
    if raw_values is None:
        return {}
    if isinstance(raw_values, dict):
        return dict(raw_values)
    if isinstance(raw_values, (bytes, bytearray)):
        try:
            decoded = json_loads(bytes(raw_values))
        except Exception:
            logger.debug("Failed to decode raw thread values bytes for enqueue-time queue staging.")
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    if isinstance(raw_values, str):
        try:
            decoded = json.loads(raw_values)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _build_checkpoint_config(
    thread_id: Any,
    saved_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": str(thread_id), "checkpoint_ns": ""}
    if isinstance(saved_config, Mapping):
        raw = saved_config.get("configurable")
        if isinstance(raw, Mapping):
            configurable.update(dict(raw))
    configurable["thread_id"] = str(thread_id)
    configurable.setdefault("checkpoint_ns", "")
    return {"configurable": configurable}


def _next_channel_version(checkpointer: Any, current_version: Any) -> Any:
    get_next_version = getattr(checkpointer, "get_next_version", None)
    if callable(get_next_version):
        try:
            return get_next_version(current_version, None)
        except TypeError:
            return get_next_version(current_version)

    if current_version is None:
        return "00000000000000000000000000000001.0000000000000000"
    if isinstance(current_version, int):
        return current_version + 1
    if isinstance(current_version, float):
        return current_version + 1.0
    try:
        prefix = int(str(current_version).split(".", 1)[0])
    except Exception:
        prefix = 0
    return f"{prefix + 1:032}.0000000000000000"


async def _persist_enqueue_time_workflow_checkpoint(
    conn: Any,
    thread: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoint_api = _get_checkpointer_api()
    checkpoint_base = _get_checkpoint_base_module()
    pregel_utils = _get_pregel_utils_module()

    thread_id = thread.get("thread_id")
    checkpoint_config = _build_checkpoint_config(thread_id)
    checkpointer = await checkpoint_api.get_checkpointer(
        conn=conn,
        use_direct_connection=True,
    )
    saved = await checkpointer.aget_tuple(checkpoint_config)

    if saved is not None:
        checkpoint = checkpoint_base.copy_checkpoint(saved.checkpoint)
        checkpoint_config = _build_checkpoint_config(thread_id, saved.config)
        previous_values = dict(saved.checkpoint.get("channel_values") or {})
        previous_versions = saved.checkpoint["channel_versions"].copy()
        raw_step = saved.metadata.get("step", -1)
        try:
            step = int(raw_step) + 1
        except Exception:
            step = 0
        raw_parents = saved.metadata.get("parents", {})
        parents = dict(raw_parents) if isinstance(raw_parents, Mapping) else {}
    else:
        checkpoint = checkpoint_base.empty_checkpoint()
        previous_values = {}
        previous_versions = {}
        step = -1
        parents = {}

    changed_channels: list[str] = []
    current_versions = checkpoint["channel_versions"]
    current_values = checkpoint["channel_values"]
    for key, value in values.items():
        previous_value = previous_values.get(key)
        if key not in current_versions or previous_value != value:
            current_versions[key] = _next_channel_version(
                checkpointer,
                current_versions.get(key),
            )
            changed_channels.append(key)
        current_values[key] = value

    checkpoint["updated_channels"] = changed_channels or None
    next_checkpoint = checkpoint_base.create_checkpoint(checkpoint, None, step)
    next_config = await checkpointer.aput(
        checkpoint_config,
        next_checkpoint,
        {
            "source": "update",
            "step": step,
            "parents": parents,
        },
        pregel_utils.get_new_channel_versions(previous_versions, current_versions),
    )
    return {
        "checkpoint": next_checkpoint,
        "config": next_config,
    }


async def _load_authoritative_thread_checkpoint(
    conn: Any,
    thread_id: Any,
    checkpoint_config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    threads_ops = _get_threads_ops()
    state_ops = getattr(threads_ops, "State", None)
    if state_ops is None or not hasattr(state_ops, "get"):
        return None

    config = _build_checkpoint_config(thread_id, checkpoint_config)
    try:
        state = await state_ops.get(conn, config, subgraphs=False)
    except Exception as exc:
        logger.debug(
            "Failed to reload authoritative thread checkpoint after enqueue-time queue staging: %s",
            exc,
        )
        return None

    return _get_thread_state_module().state_snapshot_to_thread_state(state)


async def _load_existing_thread_values(
    conn: Any,
    thread: Mapping[str, Any],
) -> dict[str, Any]:
    thread_id = thread.get("thread_id")
    row_values = _coerce_thread_values(thread.get("values"))
    if thread_id is None:
        return row_values

    authoritative_checkpoint = await _load_authoritative_thread_checkpoint(conn, thread_id)
    authoritative_values = _coerce_thread_values(
        authoritative_checkpoint.get("values") if isinstance(authoritative_checkpoint, Mapping) else None
    )
    if not authoritative_values:
        return row_values

    merged_values = dict(row_values)
    merged_values.update(authoritative_values)
    return merged_values


def _extract_original_input_from_run(run: Mapping[str, Any]) -> str | None:
    kwargs = run.get("kwargs") if isinstance(run, Mapping) else None
    if not isinstance(kwargs, Mapping):
        return None
    latest = _extract_latest_human_input(kwargs.get("input"))
    if latest:
        return latest
    command = kwargs.get("command")
    if isinstance(command, Mapping):
        resume_input = _extract_latest_human_input(command.get("resume"))
        if resume_input:
            return resume_input
    return None


def _build_enqueue_time_values(
    existing_values: Mapping[str, Any],
    run: Mapping[str, Any],
) -> dict[str, Any]:
    run_id = _stringify_uuid(run.get("run_id"))
    now = _utc_now_iso()
    original_input = _extract_original_input_from_run(run)
    values = dict(existing_values)

    values["run_id"] = run_id
    values["requested_orchestration_mode"] = _WORKFLOW_MODE
    values["resolved_orchestration_mode"] = _WORKFLOW_MODE
    values["workflow_stage"] = "queued"
    values["workflow_stage_detail"] = _QUEUED_DETAIL
    values["workflow_stage_updated_at"] = now
    values["execution_state"] = "QUEUED"
    values["task_pool"] = []
    values["verified_facts"] = {}
    values["final_result"] = None
    values["route_count"] = 0

    if original_input:
        values["original_input"] = original_input
        values["planner_goal"] = original_input

    return values


def _latest_pending_clarification_task(
    existing_values: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    task_pool = existing_values.get("task_pool")
    if not isinstance(task_pool, Sequence) or isinstance(task_pool, (str, bytes, bytearray)):
        return None

    for task in reversed(task_pool):
        if not isinstance(task, Mapping):
            continue
        if task.get("status") != "RUNNING":
            continue
        prompt = task.get("clarification_prompt")
        if isinstance(prompt, str) and prompt.strip():
            return task
    return None


def _is_clarification_resume_submission(
    existing_values: Mapping[str, Any],
    run: Mapping[str, Any],
) -> tuple[bool, str | None]:
    resume_flag, resume_run_id, resume_task_id = _run_resume_metadata(run)
    if resume_flag:
        existing_run_id = _stringify_uuid(existing_values.get("run_id"))
        if resume_run_id is None or existing_run_id is None or resume_run_id == existing_run_id:
            return True, resume_task_id

    if existing_values.get("resolved_orchestration_mode") != _WORKFLOW_MODE:
        return False, None

    pending_task = _latest_pending_clarification_task(existing_values)
    if pending_task is None:
        return False, None

    if not workflow_has_pending_clarification(dict(existing_values)):
        return False, None

    latest_input = _extract_original_input_from_run(run)
    if not latest_input:
        return False, None

    if looks_like_explicit_new_request(latest_input):
        return False, None

    task_id = pending_task.get("task_id")
    return True, str(task_id).strip() or None


def _has_same_run_authoritative_stage(
    existing_values: Mapping[str, Any],
    run: Mapping[str, Any],
) -> bool:
    run_id = _stringify_uuid(run.get("run_id"))
    if not run_id or existing_values.get("run_id") != run_id:
        return False

    stage = existing_values.get("workflow_stage")
    if not isinstance(stage, str):
        return False

    current_rank = _TERMINAL_STAGE_ORDER.get(stage)
    if current_rank is None:
        return False
    return current_rank >= _TERMINAL_STAGE_ORDER["queued"]


def _get_inmem_worker_queue_snapshot() -> tuple[int | None, int | None]:
    try:
        queue_module = import_module("langgraph_runtime_inmem.queue")
        workers = getattr(queue_module, "WORKERS", None)
        if not isinstance(workers, Mapping):
            return None, None
        max_workers = len(workers)
        active_workers = sum(
            1
            for worker in workers.values()
            if getattr(worker, "run", None) is not None or getattr(worker, "task", None) is not None
        )
        return active_workers, max_workers
    except Exception:
        return None, None


def _has_older_inflight_run(conn: Any, run: Mapping[str, Any]) -> bool:
    store = getattr(conn, "store", None)
    if not isinstance(store, Mapping):
        return False

    runs = store.get("runs")
    if not isinstance(runs, Sequence):
        return False

    current_run_id = run.get("run_id")
    current_created_at = run.get("created_at")
    if current_created_at is None:
        return False

    for candidate in runs:
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("run_id") == current_run_id:
            continue
        if candidate.get("status") not in {"pending", "running"}:
            continue
        candidate_created_at = candidate.get("created_at")
        if candidate_created_at is None:
            continue
        if candidate_created_at <= current_created_at:
            return True
    return False


async def _should_stage_workflow_queue(
    conn: Any,
    run: Mapping[str, Any],
) -> bool:
    should_stage, _ = await _queue_stage_decision(conn, run)
    return should_stage


async def _queue_stage_decision(
    conn: Any,
    run: Mapping[str, Any],
) -> tuple[bool, str]:
    if _requested_mode_from_run(run) != _WORKFLOW_MODE:
        return False, "run is not in workflow mode"
    if run.get("status") != "pending":
        return False, "run status is not pending"
    if run.get("thread_id") is None:
        return False, "run has no thread id"

    kwargs = run.get("kwargs")
    after_seconds = None
    if isinstance(kwargs, Mapping):
        config = kwargs.get("config")
        if isinstance(config, Mapping):
            configurable = config.get("configurable")
            if isinstance(configurable, Mapping):
                after_seconds = configurable.get("__after_seconds__")
    if isinstance(after_seconds, int) and after_seconds > 0:
        return False, "delayed run does not need enqueue-time queue stage"

    if _has_older_inflight_run(conn, run):
        active_workers, max_workers = _get_inmem_worker_queue_snapshot()
        if max_workers is None or active_workers is None:
            return True, "older inflight run detected"
        if active_workers >= max_workers:
            return True, "older inflight run detected with full worker pool"

    try:
        stats = await _get_runs_ops().stats(conn)
    except Exception as exc:
        logger.debug("Failed to inspect queue stats for enqueue-time queue staging: %s", exc)
        return False, "queue stats unavailable"

    n_pending = int(stats.get("n_pending") or 0)
    n_running = int(stats.get("n_running") or 0)
    if n_pending > 1:
        return True, "multiple pending runs detected"
    if n_running > 0 and n_pending > 0:
        return True, "running run already occupies queue capacity"

    return False, "run can start immediately"


async def _persist_enqueue_time_workflow_state(
    conn: Any,
    run: Mapping[str, Any],
) -> dict[str, Any] | None:
    thread_id = run.get("thread_id")
    run_id = _stringify_uuid(run.get("run_id"))
    if thread_id is None or run_id is None:
        return None

    threads_ops = _get_threads_ops()
    thread_iter = await threads_ops.get(conn, thread_id)
    thread = await _fetchone(
        thread_iter,
        not_found_detail=f"Thread {thread_id} not found while staging workflow queue state.",
    )

    existing_values = await _load_existing_thread_values(conn, thread)
    is_clarification_resume, clarification_task_id = _is_clarification_resume_submission(
        existing_values,
        run,
    )
    if is_clarification_resume:
        logger.info(
            "Skipping enqueue-time workflow queued staging for clarification resume.",
            extra={
                "thread_id": _stringify_uuid(thread_id),
                "thread_run_id": _stringify_uuid(existing_values.get("run_id")),
                "run_id": run_id,
                "classification": "clarification_resume",
                "action": "skip_enqueue_stage",
                "clarification_task_id": clarification_task_id,
            },
        )
        return None

    if _has_same_run_authoritative_stage(existing_values, run):
        logger.debug(
            "Skipping enqueue-time queued persistence because the same run already has an authoritative stage.",
            extra={"thread_id": _stringify_uuid(thread_id), "run_id": run_id},
        )
        return None

    checkpoint_values = {}
    checkpoint_result = await _persist_enqueue_time_workflow_checkpoint(
        conn,
        thread,
        _build_enqueue_time_values(existing_values, run),
    )
    checkpoint_values.update(existing_values)
    checkpoint_values.update(checkpoint_result["checkpoint"]["channel_values"])
    values = _build_enqueue_time_values(checkpoint_values, run)
    authoritative_checkpoint = await _load_authoritative_thread_checkpoint(
        conn,
        thread_id,
        checkpoint_result.get("config"),
    )
    checkpoint = authoritative_checkpoint or {
        "values": values,
        "next": [],
        "tasks": [],
    }
    await threads_ops.set_status(conn, thread_id, checkpoint, None)

    payload = {
        "type": "workflow_stage_changed",
        "run_id": run_id,
        "workflow_stage": "queued",
        "workflow_stage_detail": values["workflow_stage_detail"],
        "workflow_stage_updated_at": values["workflow_stage_updated_at"],
    }
    logger.info(
        "enqueue-time workflow queued persisted",
        extra={
            "thread_id": _stringify_uuid(thread_id),
            "run_id": run_id,
            "workflow_stage": "queued",
        },
    )
    return payload


async def _publish_enqueue_time_workflow_state(
    conn: Any,
    run: Mapping[str, Any],
) -> None:
    thread_id = _stringify_uuid(run.get("thread_id"))
    run_id = _stringify_uuid(run.get("run_id"))
    if thread_id is None or run_id is None:
        return

    should_stage, reason = await _queue_stage_decision(conn, run)
    if not should_stage:
        if _requested_mode_from_run(run) == _WORKFLOW_MODE:
            logger.debug(
                "Skipping enqueue-time workflow queued staging.",
                extra={"thread_id": thread_id, "run_id": run_id, "reason": reason},
            )
        return

    payload = await _persist_enqueue_time_workflow_state(conn, run)
    if payload is None:
        return

    await _get_runs_ops().Stream.publish(
        run.get("run_id"),
        "custom",
        json_dumpb(payload),
        thread_id=thread_id,
    )


def _patch_create_valid_run_target(module_name: str, patched_fn: Any) -> None:
    try:
        module = import_module(module_name)
    except Exception:
        return

    if hasattr(module, "create_valid_run"):
        setattr(module, "create_valid_run", patched_fn)


def install_enqueue_time_workflow_stage_patch() -> None:
    global _PATCH_INSTALLED

    if _PATCH_INSTALLED:
        return

    try:
        run_module = import_module("langgraph_api.models.run")
    except Exception as exc:
        logger.debug("Skipping enqueue-time workflow stage patch installation: %s", exc)
        return

    original_create_valid_run = getattr(run_module, "create_valid_run")
    if getattr(original_create_valid_run, _PATCH_SENTINEL_ATTR, False):
        _PATCH_INSTALLED = True
        return

    async def _patched_create_valid_run(*args: Any, **kwargs: Any):
        run = await original_create_valid_run(*args, **kwargs)
        conn = args[0] if args else kwargs.get("conn")
        if conn is None:
            return run

        try:
            await _publish_enqueue_time_workflow_state(conn, run)
        except Exception:
            logger.exception("Failed to publish enqueue-time workflow queued stage.")
        return run

    setattr(_patched_create_valid_run, _PATCH_SENTINEL_ATTR, True)
    setattr(run_module, "create_valid_run", _patched_create_valid_run)
    _patch_create_valid_run_target("langgraph_api.api.runs", _patched_create_valid_run)
    _patch_create_valid_run_target("langgraph_api.cron_scheduler", _patched_create_valid_run)

    _PATCH_INSTALLED = True
