from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.outputs import Generation, LLMResult

from src.observability.llm_callback import ObservabilityCallbackHandler
from src.observability.metrics import WorkflowMetrics
from src.observability.node_wrapper import traced_node


def _make_fallback_metrics() -> WorkflowMetrics:
    WorkflowMetrics.reset()
    metrics = WorkflowMetrics.get()
    metrics._use_otel = False
    metrics._otel_counters = {}
    metrics._otel_histograms = {}
    return metrics


def _counter_value(snapshot: dict, metric_name: str, label_fragment: str) -> float:
    for key, value in snapshot["counters"].items():
        if key.startswith(f"{metric_name}|") and label_fragment in key:
            return value
    raise AssertionError(f"Missing counter for {metric_name} with labels containing {label_fragment!r}")


class TestObservabilityCallbackHandler:
    def setup_method(self):
        WorkflowMetrics.reset()

    def test_records_duration_and_tokens_from_openai_usage(self):
        metrics = _make_fallback_metrics()
        handler = ObservabilityCallbackHandler(node_hint="planner")
        run_id = uuid4()

        asyncio.run(handler.on_llm_start({}, ["hello"], run_id=run_id))
        response = LLMResult(
            generations=[[Generation(text="ok")]],
            llm_output={
                "model_name": "gpt-test",
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 200},
            },
        )
        asyncio.run(handler.on_llm_end(response, run_id=run_id))

        snapshot = metrics.snapshot()
        assert _counter_value(snapshot, "llm.call.total", "model=gpt-test,node=planner") == 1
        assert _counter_value(snapshot, "llm.tokens.total", "direction=input,model=gpt-test") == 100
        assert _counter_value(snapshot, "llm.tokens.total", "direction=output,model=gpt-test") == 200

        hist = next(
            value
            for key, value in snapshot["histograms"].items()
            if key.startswith("llm.call.duration_ms|") and "model=gpt-test,node=planner" in key
        )
        assert hist["count"] == 1
        assert hist["max"] >= 0

    def test_handles_malformed_response_without_breaking_metrics(self):
        metrics = _make_fallback_metrics()
        handler = ObservabilityCallbackHandler(node_hint="router")
        run_id = uuid4()

        asyncio.run(handler.on_llm_start({}, ["hello"], run_id=run_id))
        malformed = SimpleNamespace(llm_output={"model": "broken-model"}, generations=[[SimpleNamespace()]])
        asyncio.run(handler.on_llm_end(malformed, run_id=run_id))

        snapshot = metrics.snapshot()
        assert _counter_value(snapshot, "llm.call.total", "model=broken-model,node=router") == 1
        assert _counter_value(snapshot, "llm.tokens.total", "direction=input,model=broken-model") == 0
        assert _counter_value(snapshot, "llm.tokens.total", "direction=output,model=broken-model") == 0

    def test_error_callback_cleans_up_start_times(self):
        handler = ObservabilityCallbackHandler()
        run_id = uuid4()

        asyncio.run(handler.on_llm_start({}, ["hello"], run_id=run_id))
        assert run_id in handler._start_times

        asyncio.run(handler.on_llm_error(RuntimeError("boom"), run_id=run_id))
        assert run_id not in handler._start_times


class TestTracedNode:
    def test_preserves_return_value_and_records_execution_state(self):
        async def node(state):
            assert state["run_id"] == "run-1"
            return {"execution_state": "DONE", "value": 42}

        wrapped = traced_node("planner")(node)
        result = asyncio.run(
            wrapped(
                {
                    "run_id": "run-1",
                    "route_count": 2,
                    "task_pool": [{"task_id": "task-1", "status": "RUNNING"}],
                }
            )
        )

        assert result == {"execution_state": "DONE", "value": 42}

    def test_does_not_swallow_exceptions(self):
        async def node(_state):
            raise ValueError("boom")

        wrapped = traced_node("executor")(node)

        with pytest.raises(ValueError, match="boom"):
            asyncio.run(wrapped({}))


def test_create_chat_model_attaches_observability_callback(monkeypatch):
    from src.config.app_config import AppConfig
    from src.config.model_config import ModelConfig
    from src.config.sandbox_config import SandboxConfig
    from src.models import factory as factory_module

    app_config = AppConfig(
        models=[
            ModelConfig(
                name="demo-model",
                display_name="demo-model",
                description=None,
                use="fake.module:FakeChatModel",
                model="demo-model",
                supports_thinking=False,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )

    class FakeChatModel:
        def __init__(self, **kwargs):
            self.callbacks = kwargs.get("callbacks")

    monkeypatch.setattr(factory_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(factory_module, "resolve_class", lambda *_args, **_kwargs: FakeChatModel)
    monkeypatch.setattr(factory_module, "is_tracing_enabled", lambda: False)

    model = factory_module.create_chat_model(name="demo-model")

    assert model.callbacks is not None
    assert any(isinstance(callback, ObservabilityCallbackHandler) for callback in model.callbacks)


def test_compile_multi_agent_graph_wraps_nodes_with_tracing(monkeypatch):
    from src.agents import graph as graph_module

    async def planner_node(*_args, **_kwargs):
        return {"execution_state": "DONE"}

    async def router_node(*_args, **_kwargs):
        return {"execution_state": "DONE"}

    async def executor_node(*_args, **_kwargs):
        return {"execution_state": "DONE"}

    captured_nodes: dict[str, object] = {}

    class FakeStateGraph:
        def __init__(self, _state_type):
            pass

        def add_node(self, name, fn):
            captured_nodes[name] = fn

        def add_edge(self, *_args, **_kwargs):
            pass

        def add_conditional_edges(self, *_args, **_kwargs):
            pass

        def compile(self, checkpointer=None):
            return {"nodes": captured_nodes, "checkpointer": checkpointer}

    monkeypatch.setattr(graph_module, "planner_node", planner_node)
    monkeypatch.setattr(graph_module, "router_node", router_node)
    monkeypatch.setattr(graph_module, "executor_node", executor_node)
    monkeypatch.setattr(graph_module, "StateGraph", FakeStateGraph)

    graph_module._compile_multi_agent_graph()

    assert captured_nodes["planner"] is not planner_node
    assert captured_nodes["router"] is not router_node
    assert captured_nodes["executor"] is not executor_node
    assert getattr(captured_nodes["planner"], "__wrapped__", None) is planner_node
    assert getattr(captured_nodes["router"], "__wrapped__", None) is router_node
    assert getattr(captured_nodes["executor"], "__wrapped__", None) is executor_node
