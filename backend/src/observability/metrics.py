"""Metrics facade — uses OpenTelemetry instruments when available, in-memory fallback otherwise."""

import math
import threading
from typing import Any


class WorkflowMetrics:
    """Singleton metrics collector for workflow observability."""

    _instance: "WorkflowMetrics | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._use_otel = False
        self._otel_counters: dict[str, Any] = {}
        self._otel_histograms: dict[str, Any] = {}

        # In-memory fallback
        self._mem_lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

        self._try_init_otel()

    @classmethod
    def get(cls) -> "WorkflowMetrics":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — primarily for testing."""
        with cls._lock:
            cls._instance = None

    def _try_init_otel(self) -> None:
        try:
            from opentelemetry import metrics as otel_metrics

            meter = otel_metrics.get_meter("deer-flow")
            self._otel_counters = {
                "workflow.total": meter.create_counter("workflow.total", description="Workflow start count"),
                "task.total": meter.create_counter("task.total", description="Task creation count"),
                "task.failure.total": meter.create_counter("task.failure.total", description="Task failure count"),
                "llm.call.total": meter.create_counter("llm.call.total", description="LLM call count"),
                "llm.tokens.total": meter.create_counter("llm.tokens.total", description="Token consumption"),
                "mcp.call.total": meter.create_counter("mcp.call.total", description="MCP tool call count"),
                "intervention.total": meter.create_counter("intervention.total", description="Intervention trigger count"),
                "helper.retry.total": meter.create_counter("helper.retry.total", description="Helper retry count"),
            }
            self._otel_histograms = {
                "workflow.duration_ms": meter.create_histogram("workflow.duration_ms", unit="ms", description="End-to-end workflow duration"),
                "task.duration_ms": meter.create_histogram("task.duration_ms", unit="ms", description="Single task duration"),
                "llm.call.duration_ms": meter.create_histogram("llm.call.duration_ms", unit="ms", description="LLM call latency"),
                "mcp.call.duration_ms": meter.create_histogram("mcp.call.duration_ms", unit="ms", description="MCP tool call latency"),
            }
            self._use_otel = True
        except ImportError:
            self._use_otel = False

    def _inc_counter(self, name: str, value: float, labels: dict[str, str]) -> None:
        if self._use_otel and name in self._otel_counters:
            self._otel_counters[name].add(value, labels)
        else:
            key = f"{name}|{_labels_key(labels)}"
            with self._mem_lock:
                self._counters[key] = self._counters.get(key, 0) + value

    def _record_histogram(self, name: str, value: float, labels: dict[str, str]) -> None:
        if self._use_otel and name in self._otel_histograms:
            self._otel_histograms[name].record(value, labels)
        else:
            key = f"{name}|{_labels_key(labels)}"
            with self._mem_lock:
                self._histograms.setdefault(key, []).append(value)

    # ── Public recording methods ──

    def record_workflow_start(self, run_id: str, mode: str) -> None:
        self._inc_counter("workflow.total", 1, {"run_id": run_id, "mode": mode})

    def record_workflow_end(self, run_id: str, mode: str, duration_ms: float, status: str) -> None:
        self._record_histogram("workflow.duration_ms", duration_ms, {"mode": mode, "status": status})

    def record_task(self, task_id: str, agent: str, status: str, duration_ms: float) -> None:
        self._inc_counter("task.total", 1, {"agent": agent, "status": status})
        self._record_histogram("task.duration_ms", duration_ms, {"agent": agent, "status": status})
        if status in ("FAILED", "failed"):
            self._inc_counter("task.failure.total", 1, {"agent": agent})

    def record_llm_call(self, model: str, node: str, duration_ms: float, input_tokens: int, output_tokens: int) -> None:
        labels = {"model": model, "node": node}
        self._inc_counter("llm.call.total", 1, labels)
        self._record_histogram("llm.call.duration_ms", duration_ms, labels)
        self._inc_counter("llm.tokens.total", input_tokens, {"model": model, "direction": "input"})
        self._inc_counter("llm.tokens.total", output_tokens, {"model": model, "direction": "output"})

    def record_mcp_call(self, tool_name: str, agent: str, duration_ms: float, success: bool) -> None:
        self._inc_counter("mcp.call.total", 1, {"tool": tool_name, "agent": agent, "success": str(success)})
        self._record_histogram("mcp.call.duration_ms", duration_ms, {"tool": tool_name, "agent": agent})

    def record_intervention(self, agent: str, tool: str, risk_level: str, tenant_id: str | None = None, user_id: str | None = None) -> None:
        labels = {"agent": agent, "tool": tool, "risk_level": risk_level}
        if tenant_id:
            labels["tenant_id"] = tenant_id
        if user_id:
            labels["user_id"] = user_id
        self._inc_counter("intervention.total", 1, labels)

    def record_helper_retry(self, parent_task_id: str, agent: str) -> None:
        self._inc_counter("helper.retry.total", 1, {"parent_task_id": parent_task_id, "agent": agent})

    # ── Snapshot for /debug/metrics ──

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of in-memory metrics."""
        with self._mem_lock:
            counters = dict(self._counters)
            histograms = {
                key: _histogram_stats(values)
                for key, values in self._histograms.items()
            }
        return {"counters": counters, "histograms": histograms}


def _labels_key(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _histogram_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "avg": 0, "max": 0, "p95": 0}
    sorted_values = sorted(values)
    count = len(sorted_values)
    avg = sum(sorted_values) / count
    max_val = sorted_values[-1]
    p95_idx = min(math.ceil(count * 0.95) - 1, count - 1)
    p95 = sorted_values[max(p95_idx, 0)]
    return {"count": count, "avg": round(avg, 2), "max": round(max_val, 2), "p95": round(p95, 2)}
