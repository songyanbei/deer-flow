from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.agents.runtime_queue_stage import (
    _persist_enqueue_time_workflow_checkpoint,
    _persist_enqueue_time_workflow_state,
    _publish_enqueue_time_workflow_state,
    _should_stage_workflow_queue,
)


def test_should_stage_workflow_queue_when_older_inflight_run_exists():
    now = datetime.now(UTC)
    older_run = {
        "run_id": uuid4(),
        "thread_id": uuid4(),
        "status": "running",
        "created_at": now - timedelta(seconds=5),
        "kwargs": {
            "config": {
                "configurable": {
                    "requested_orchestration_mode": "workflow",
                }
            }
        },
    }
    new_run = {
        "run_id": uuid4(),
        "thread_id": uuid4(),
        "status": "pending",
        "created_at": now,
        "kwargs": {
            "config": {
                "configurable": {
                    "requested_orchestration_mode": "workflow",
                }
            }
        },
    }
    conn = SimpleNamespace(store={"runs": [older_run, new_run], "threads": []})

    async def _run():
        assert await _should_stage_workflow_queue(conn, new_run) is True

    asyncio.run(_run())


def test_should_not_stage_workflow_queue_without_backlog():
    now = datetime.now(UTC)
    new_run = {
        "run_id": uuid4(),
        "thread_id": uuid4(),
        "status": "pending",
        "created_at": now,
        "kwargs": {
            "config": {
                "configurable": {
                    "requested_orchestration_mode": "workflow",
                }
            }
        },
    }
    conn = SimpleNamespace(store={"runs": [new_run], "threads": []})

    async def _run():
        assert await _should_stage_workflow_queue(conn, new_run) is False

    asyncio.run(_run())


def test_persist_enqueue_time_workflow_state_uses_thread_status_path_and_preserves_messages():
    from src.agents import runtime_queue_stage as stage_module

    thread_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    existing_values = {
        "messages": [{"role": "assistant", "content": "existing history"}],
        "run_id": "run_old",
        "workflow_stage": "summarizing",
        "task_pool": [{"task_id": "old"}],
        "verified_facts": {"old": {"summary": "old"}},
        "final_result": "old result",
    }
    conn = SimpleNamespace(
        store={
            "threads": [
                {
                    "thread_id": thread_id,
                    "values": existing_values,
                    "status": "idle",
                    "updated_at": now - timedelta(minutes=1),
                    "state_updated_at": now - timedelta(minutes=1),
                }
            ],
            "runs": [],
        }
    )
    run = {
        "run_id": run_id,
        "thread_id": thread_id,
        "status": "pending",
        "created_at": now,
        "kwargs": {
            "input": [
                {"role": "user", "content": "Book a meeting room tomorrow morning."}
            ],
            "config": {
                "configurable": {
                    "requested_orchestration_mode": "workflow",
                }
            },
        },
    }
    conn.store["runs"].append(run)
    authoritative_values = {
        **existing_values,
        "run_id": str(run_id),
        "requested_orchestration_mode": "workflow",
        "resolved_orchestration_mode": "workflow",
        "workflow_stage": "queued",
        "workflow_stage_detail": "\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u6392\u961f\u542f\u52a8...",
        "workflow_stage_updated_at": now.isoformat(),
        "execution_state": "QUEUED",
        "task_pool": [],
        "verified_facts": {},
        "final_result": None,
        "route_count": 0,
        "original_input": "Book a meeting room tomorrow morning.",
        "planner_goal": "Book a meeting room tomorrow morning.",
    }

    async def _fake_get(_conn, requested_thread_id):
        async def _iterator():
            for thread in _conn.store["threads"]:
                if thread["thread_id"] == requested_thread_id:
                    yield thread
                    return

        return _iterator()

    async def _fake_set_status(_conn, requested_thread_id, checkpoint, _exception):
        for thread in _conn.store["threads"]:
            if thread["thread_id"] != requested_thread_id:
                continue
            thread["values"] = checkpoint["values"]
            thread["status"] = "busy"
            thread["updated_at"] = datetime.now(UTC)
            thread["state_updated_at"] = datetime.now(UTC)
            return
        raise AssertionError("Expected thread to exist when persisting queue state.")

    fake_threads = SimpleNamespace(get=_fake_get, set_status=_fake_set_status)

    async def _run():
        with patch("src.agents.runtime_queue_stage._get_threads_ops", return_value=fake_threads):
            with patch.object(
                stage_module,
                "_persist_enqueue_time_workflow_checkpoint",
                AsyncMock(
                    return_value={
                        "checkpoint": {"channel_values": authoritative_values},
                        "config": {
                            "configurable": {
                                "thread_id": str(thread_id),
                                "checkpoint_ns": "",
                                "checkpoint_id": "cp-queued",
                            }
                        },
                    }
                ),
            ):
                with patch.object(
                    stage_module,
                    "_load_authoritative_thread_checkpoint",
                    AsyncMock(
                        return_value={
                            "values": authoritative_values,
                            "next": [],
                            "tasks": [],
                        }
                    ),
                ):
                    payload = await _persist_enqueue_time_workflow_state(conn, run)

        assert payload is not None
        assert payload["workflow_stage"] == "queued"
        assert payload["run_id"] == str(run_id)

    asyncio.run(_run())

    updated_thread = conn.store["threads"][0]
    assert updated_thread["status"] == "busy"
    assert updated_thread["values"]["messages"] == existing_values["messages"]
    assert updated_thread["values"]["run_id"] == str(run_id)
    assert updated_thread["values"]["workflow_stage"] == "queued"
    assert updated_thread["values"]["workflow_stage_detail"] == "\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u6392\u961f\u542f\u52a8..."
    assert updated_thread["values"]["task_pool"] == []
    assert updated_thread["values"]["verified_facts"] == {}
    assert updated_thread["values"]["final_result"] is None
    assert updated_thread["values"]["original_input"] == "Book a meeting room tomorrow morning."
    assert updated_thread["values"]["planner_goal"] == "Book a meeting room tomorrow morning."


def test_persist_enqueue_time_workflow_checkpoint_writes_history_with_queued_values():
    thread_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    thread = {
        "thread_id": thread_id,
        "values": {
            "messages": [{"role": "user", "content": "existing history"}],
            "kept": "thread-row-value",
        },
    }
    saved = SimpleNamespace(
        checkpoint={
            "v": 4,
            "id": "cp-1",
            "ts": (now - timedelta(seconds=5)).isoformat(),
            "channel_values": {
                "messages": [{"role": "user", "content": "existing history"}],
                "kept": "checkpoint-value",
                "workflow_stage": "acknowledged",
            },
            "channel_versions": {
                "messages": "00000000000000000000000000000001.1000000000000000",
                "kept": "00000000000000000000000000000001.2000000000000000",
                "workflow_stage": "00000000000000000000000000000001.3000000000000000",
            },
            "versions_seen": {},
            "pending_sends": [],
            "updated_channels": None,
        },
        config={
            "configurable": {
                "thread_id": str(thread_id),
                "checkpoint_ns": "",
                "checkpoint_id": "cp-1",
            }
        },
        metadata={"step": 0, "parents": {}},
    )

    def _next_version(current, _channel=None):
        if current is None:
            return "00000000000000000000000000000001.0000000000000000"
        prefix = int(str(current).split(".", 1)[0]) + 1
        return f"{prefix:032}.0000000000000000"

    fake_checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(return_value=saved),
        aput=AsyncMock(
            return_value={
                "configurable": {
                    "thread_id": str(thread_id),
                    "checkpoint_ns": "",
                    "checkpoint_id": "cp-2",
                }
            }
        ),
        get_next_version=_next_version,
    )
    values = {
        "messages": [{"role": "user", "content": "existing history"}],
        "kept": "thread-row-value",
        "run_id": str(run_id),
        "workflow_stage": "queued",
        "workflow_stage_detail": "\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u6392\u961f\u542f\u52a8...",
        "workflow_stage_updated_at": now.isoformat(),
    }

    async def _run():
        with patch(
            "src.agents.runtime_queue_stage._get_checkpointer_api",
            return_value=SimpleNamespace(get_checkpointer=AsyncMock(return_value=fake_checkpointer)),
        ):
            result = await _persist_enqueue_time_workflow_checkpoint(
                SimpleNamespace(),
                thread,
                values,
            )

        assert result["config"]["configurable"]["checkpoint_id"] == "cp-2"

    asyncio.run(_run())

    aput_args = fake_checkpointer.aput.await_args.args
    persisted_config = aput_args[0]
    persisted_checkpoint = aput_args[1]
    persisted_metadata = aput_args[2]
    persisted_new_versions = aput_args[3]

    assert persisted_config["configurable"]["checkpoint_id"] == "cp-1"
    assert persisted_checkpoint["channel_values"]["messages"] == values["messages"]
    assert persisted_checkpoint["channel_values"]["kept"] == "thread-row-value"
    assert persisted_checkpoint["channel_values"]["workflow_stage"] == "queued"
    assert persisted_checkpoint["channel_values"]["workflow_stage_detail"] == values["workflow_stage_detail"]
    assert "kept" in persisted_new_versions
    assert "workflow_stage" in persisted_new_versions
    assert "workflow_stage_detail" in persisted_new_versions
    assert persisted_metadata["source"] == "update"
    assert persisted_metadata["step"] == 1


def test_enqueue_time_stage_publish_uses_existing_custom_contract():
    from src.agents import runtime_queue_stage as stage_module

    now = datetime.now(UTC)
    thread_id = uuid4()
    run_id = uuid4()
    run = {
        "run_id": run_id,
        "thread_id": thread_id,
        "status": "pending",
        "created_at": now,
        "kwargs": {
            "config": {
                "configurable": {
                    "requested_orchestration_mode": "workflow",
                }
            }
        },
    }

    async def _run():
        fake_runs = SimpleNamespace(Stream=SimpleNamespace(publish=AsyncMock()))
        payload = {
            "type": "workflow_stage_changed",
            "run_id": str(run_id),
            "workflow_stage": "queued",
            "workflow_stage_detail": "\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u6392\u961f\u542f\u52a8...",
            "workflow_stage_updated_at": now.isoformat(),
        }

        with patch.object(stage_module, "_queue_stage_decision", AsyncMock(return_value=(True, "older inflight run detected"))):
            with patch.object(stage_module, "_persist_enqueue_time_workflow_state", AsyncMock(return_value=payload)):
                with patch.object(stage_module, "_get_runs_ops", return_value=fake_runs):
                    await stage_module._publish_enqueue_time_workflow_state(SimpleNamespace(), run)

        fake_runs.Stream.publish.assert_awaited_once()
        args = fake_runs.Stream.publish.await_args.args
        kwargs = fake_runs.Stream.publish.await_args.kwargs
        event_payload = json.loads(args[2].decode("utf-8"))
        assert args[0] == run_id
        assert args[1] == "custom"
        assert kwargs["thread_id"] == str(thread_id)
        assert event_payload["type"] == "workflow_stage_changed"
        assert event_payload["workflow_stage"] == "queued"
        assert event_payload["run_id"] == str(run_id)

    asyncio.run(_run())
