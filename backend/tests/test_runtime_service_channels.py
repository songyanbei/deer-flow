"""Phase 0 regression tests for trusted-context submit channel hygiene.

Covers:
- D0.1: Gateway ``runtime_service.start_stream`` must submit to the remote
  LangGraph API with **context only**, never with both
  ``config.configurable`` and ``context`` populated. LG1.x rejects dual
  channel with HTTP 400.
- D0.2: ``task_tool`` -> ``SubagentExecutor`` must propagate the parent's
  trusted ``thread_context`` and ``auth_user`` into the child's
  ``run_config["configurable"]`` so the child's ThreadDataMiddleware
  resolves the same {tenant_id, user_id, thread_id} scope and identity
  guards stay enforced.

These tests are designed to fail on the pre-Phase-0 code path:
- Old ``start_stream`` injected ``configurable`` alongside ``context``.
- Old ``SubagentExecutor`` did not accept ``thread_context`` / ``auth_user``.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, patch

import pytest

from src.gateway.runtime_service import start_resume_stream, start_stream
from src.subagents.executor import SubagentExecutor


# ── D0.1: Gateway remote submit kwargs ────────────────────────────────


class _CapturingRuns:
    """Minimal stand-in for ``langgraph_sdk.client.runs`` that records the
    kwargs passed to ``stream(...)`` and yields a single dummy chunk."""

    def __init__(self) -> None:
        self.captured_args: tuple = ()
        self.captured_kwargs: dict = {}

    def stream(self, *args, **kwargs):
        self.captured_args = args
        self.captured_kwargs = kwargs

        async def _aiter():
            # Yield one chunk so start_stream's first-chunk fetch succeeds.
            yield ("ack", {"thread_id": args[0]})

        return _aiter()


def _build_trusted_context() -> dict:
    """Build a context dict mirroring what ``stream_runtime_message`` produces."""
    return {
        "thread_id": "thread-xyz",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "username": "Alice",
        "allowed_agents": ["research-agent"],
        "group_key": "team-alpha",
        "thread_context": {
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "thread_id": "thread-xyz",
            "memory_root": "/tmp/memory",
        },
        "auth_user": {
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "name": "Alice",
            "employee_no": "E001",
            "target_system": "deerflow",
            "role": "user",
        },
    }


@patch("src.gateway.runtime_service._get_client")
def test_start_stream_does_not_pass_configurable(mock_get_client):
    """D0.1: SDK kwargs must not include ``config['configurable']``."""
    runs = _CapturingRuns()
    client = MagicMock()
    client.runs = runs
    mock_get_client.return_value = client

    context = _build_trusted_context()

    asyncio.run(
        start_stream(
            thread_id="thread-xyz",
            message="hello",
            context=context,
        )
    )

    config = runs.captured_kwargs.get("config")
    assert isinstance(config, dict), "start_stream must pass a config dict"
    assert "configurable" not in config, (
        "Phase 0 regression: start_stream must not send 'configurable' "
        "alongside 'context' (LG1.x rejects dual channel with HTTP 400)."
    )
    assert config == {"recursion_limit": 1000}


@patch("src.gateway.runtime_service._get_client")
def test_start_stream_passes_trusted_context_through_context_channel(mock_get_client):
    """D0.1: identity must reach upstream via the ``context`` channel."""
    runs = _CapturingRuns()
    client = MagicMock()
    client.runs = runs
    mock_get_client.return_value = client

    context = _build_trusted_context()

    asyncio.run(
        start_stream(
            thread_id="thread-xyz",
            message="hello",
            context=context,
        )
    )

    sent_ctx = runs.captured_kwargs.get("context")
    assert isinstance(sent_ctx, dict)
    for key in ("thread_id", "tenant_id", "user_id", "thread_context", "auth_user"):
        assert key in sent_ctx, f"context must carry trusted field '{key}'"
    assert sent_ctx["thread_context"]["thread_id"] == "thread-xyz"
    assert sent_ctx["auth_user"]["user_id"] == "user-1"


@patch("src.gateway.runtime_service._get_client")
def test_start_stream_does_not_mutate_caller_context(mock_get_client):
    """D0.1: caller-provided context dict must not be mutated by start_stream."""
    runs = _CapturingRuns()
    client = MagicMock()
    client.runs = runs
    mock_get_client.return_value = client

    context = _build_trusted_context()
    snapshot = dict(context)

    asyncio.run(
        start_stream(
            thread_id="thread-xyz",
            message="hello",
            context=context,
        )
    )

    assert context == snapshot, "start_stream must not mutate caller-owned context"
    # The dict sent upstream must be a distinct object from the caller's.
    assert runs.captured_kwargs.get("context") is not context


# ── D2.1: Gateway resume submit kwargs ────────────────────────────────


@patch("src.gateway.runtime_service._get_client")
def test_start_resume_stream_single_channel(mock_get_client):
    """D2.1: resume path must submit context-only, never dual-channel."""
    runs = _CapturingRuns()
    client = MagicMock()
    client.runs = runs
    mock_get_client.return_value = client

    context = _build_trusted_context()
    checkpoint = {"checkpoint_id": "ckpt-1"}

    asyncio.run(
        start_resume_stream(
            thread_id="thread-xyz",
            context=context,
            message="resume now",
            checkpoint=checkpoint,
        )
    )

    config = runs.captured_kwargs.get("config")
    assert config == {"recursion_limit": 1000}
    assert "configurable" not in config

    sent_ctx = runs.captured_kwargs.get("context")
    assert sent_ctx["thread_context"]["thread_id"] == "thread-xyz"
    assert runs.captured_kwargs.get("checkpoint") == checkpoint
    # Resume with a message still builds a human input payload.
    input_payload = runs.captured_kwargs.get("input")
    assert isinstance(input_payload, dict)
    assert input_payload["messages"][0]["content"][0]["text"] == "resume now"

    # D2.1 kwargs contract — resume preserves the legacy InterventionCard
    # stream contract: resumable stream + messages-tuple mode.
    assert runs.captured_kwargs.get("stream_resumable") is True
    stream_mode = runs.captured_kwargs.get("stream_mode")
    assert stream_mode == ["values", "messages-tuple", "custom"]


@patch("src.gateway.runtime_service._get_client")
def test_start_resume_stream_command_only(mock_get_client):
    """D2.1: command-only resume sends input=None and forwards Command."""
    runs = _CapturingRuns()
    client = MagicMock()
    client.runs = runs
    mock_get_client.return_value = client

    context = _build_trusted_context()

    asyncio.run(
        start_resume_stream(
            thread_id="thread-xyz",
            context=context,
            message=None,
            command={"resume": {"answer": "yes"}},
        )
    )

    assert runs.captured_kwargs.get("input") is None
    assert runs.captured_kwargs.get("command") == {"resume": {"answer": "yes"}}
    assert "configurable" not in runs.captured_kwargs.get("config", {})


# ── D0.2: Subagent trusted context propagation ────────────────────────


def test_subagent_executor_accepts_thread_context_and_auth_user():
    """D0.2: SubagentExecutor must accept ``thread_context`` and ``auth_user``."""
    sig = inspect.signature(SubagentExecutor.__init__)
    assert "thread_context" in sig.parameters
    assert "auth_user" in sig.parameters
    # Both should be optional with a default of ``None``.
    assert sig.parameters["thread_context"].default is None
    assert sig.parameters["auth_user"].default is None


def test_subagent_executor_stores_trusted_fields():
    """D0.2: stored trusted fields are exposed on the instance for the
    subsequent ``execute()`` to forward into child ``configurable``."""
    from src.subagents.config import SubagentConfig

    config = SubagentConfig(
        name="general-purpose",
        description="test",
        system_prompt="sys",
        model="inherit",
        max_turns=5,
        timeout_seconds=60,
    )

    thread_context = {"thread_id": "t1", "tenant_id": "ta", "user_id": "u1"}
    auth_user = {"tenant_id": "ta", "user_id": "u1", "name": "Alice"}

    executor = SubagentExecutor(
        config=config,
        tools=[],
        thread_id="t1",
        tenant_id="ta",
        user_id="u1",
        thread_context=thread_context,
        auth_user=auth_user,
    )

    assert executor.thread_context == thread_context
    assert executor.auth_user == auth_user


def test_subagent_execute_forwards_trusted_fields_into_child_configurable():
    """D0.2: when ``execute()`` calls ``agent.stream``, the child's
    ``run_config['configurable']`` must include both ``thread_context`` and
    ``auth_user`` so the child's ThreadDataMiddleware and identity_guard
    receive the parent's trusted scope."""
    from src.subagents.config import SubagentConfig

    config = SubagentConfig(
        name="general-purpose",
        description="test",
        system_prompt="sys",
        model="inherit",
        max_turns=5,
        timeout_seconds=60,
    )

    thread_context = {"thread_id": "t1", "tenant_id": "ta", "user_id": "u1"}
    auth_user = {"tenant_id": "ta", "user_id": "u1", "name": "Alice"}

    executor = SubagentExecutor(
        config=config,
        tools=[],
        thread_id="t1",
        tenant_id="ta",
        user_id="u1",
        thread_context=thread_context,
        auth_user=auth_user,
    )

    captured = {}

    class _StubAgent:
        def stream(self, state, *, config, context, stream_mode):
            captured["config"] = config
            captured["context"] = context
            return iter([])  # no chunks; execute() will treat as no-op

    with patch.object(executor, "_create_agent", return_value=_StubAgent()):
        result = executor.execute("do something")

    cfg = captured.get("config") or {}
    configurable = cfg.get("configurable") or {}
    assert configurable.get("thread_id") == "t1"
    assert configurable.get("tenant_id") == "ta"
    assert configurable.get("user_id") == "u1"
    assert configurable.get("thread_context") == thread_context, (
        "Phase 0 regression: child configurable must carry parent's "
        "trusted thread_context so child ThreadDataMiddleware resolves the "
        "same {tenant, user, thread} scope."
    )
    assert configurable.get("auth_user") == auth_user, (
        "Phase 0 regression: child configurable must carry parent's "
        "auth_user so identity_guard stays enforced on child tool calls."
    )

    # Local pregel path is dual-channel by design — context must also carry
    # tenant/user/thread ids.
    assert captured["context"].get("tenant_id") == "ta"
    # execute() should not crash with our empty-stream stub.
    assert result.error is None or result.status.value == "completed"


def test_subagent_execute_omits_trusted_fields_when_parent_missing_them():
    """D0.2: when the parent did not provide trusted context, the child must
    not synthesize fake values. Absence stays absent — fail closed downstream."""
    from src.subagents.config import SubagentConfig

    config = SubagentConfig(
        name="general-purpose",
        description="test",
        system_prompt="sys",
        model="inherit",
        max_turns=5,
        timeout_seconds=60,
    )

    executor = SubagentExecutor(
        config=config,
        tools=[],
        thread_id="t1",
        tenant_id="ta",
        user_id="u1",
        # thread_context and auth_user intentionally omitted
    )

    captured = {}

    class _StubAgent:
        def stream(self, state, *, config, context, stream_mode):
            captured["config"] = config
            return iter([])

    with patch.object(executor, "_create_agent", return_value=_StubAgent()):
        executor.execute("do something")

    configurable = (captured.get("config") or {}).get("configurable") or {}
    assert "thread_context" not in configurable
    assert "auth_user" not in configurable


def test_subagent_execute_rejects_empty_trusted_fields():
    """D0.2 defense-in-depth: empty dicts must not be forwarded. An empty
    thread_context would make the child's ThreadDataMiddleware attempt to
    resolve an invalid scope instead of falling back to the loose-id path;
    an auth_user without user_id cannot be enforced by identity_guard."""
    from src.subagents.config import SubagentConfig

    config = SubagentConfig(
        name="general-purpose",
        description="test",
        system_prompt="sys",
        model="inherit",
        max_turns=5,
        timeout_seconds=60,
    )

    executor = SubagentExecutor(
        config=config,
        tools=[],
        thread_id="t1",
        tenant_id="ta",
        user_id="u1",
        thread_context={},  # empty dict must not be forwarded
        auth_user={"name": "Alice"},  # missing user_id must not be forwarded
    )

    captured = {}

    class _StubAgent:
        def stream(self, state, *, config, context, stream_mode):
            captured["config"] = config
            return iter([])

    with patch.object(executor, "_create_agent", return_value=_StubAgent()):
        executor.execute("do something")

    configurable = (captured.get("config") or {}).get("configurable") or {}
    assert "thread_context" not in configurable
    assert "auth_user" not in configurable
