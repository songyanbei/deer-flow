"""Observability configuration definitions."""

import os
from typing import Literal

from pydantic import BaseModel, Field


class OtelConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable OpenTelemetry export")
    endpoint: str = Field(default="http://localhost:4317", description="OTLP collector endpoint")
    service_name: str = Field(default="deer-flow", description="OTel service name")


class DecisionLogConfig(BaseModel):
    enabled: bool = Field(default=True, description="Enable structured decision logging")
    output: Literal["stdout", "file"] = Field(default="stdout", description="Decision log output target")
    file_path: str = Field(default="", description="Decision log file path (when output=file)")


class ObservabilityConfig(BaseModel):
    otel: OtelConfig = Field(default_factory=OtelConfig)
    decision_log: DecisionLogConfig = Field(default_factory=DecisionLogConfig)
    metrics_enabled: bool = Field(default=True, description="Enable in-memory metrics collection")
    metrics_expose_endpoint: bool = Field(default=True, description="Expose /debug/metrics endpoint")


_observability_config: ObservabilityConfig | None = None


def get_observability_config() -> ObservabilityConfig:
    """Get observability config, reading from environment variables."""
    global _observability_config
    if _observability_config is not None:
        return _observability_config

    otel = OtelConfig(
        enabled=os.environ.get("OTEL_ENABLED", "false").strip().lower() == "true",
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        service_name=os.environ.get("OTEL_SERVICE_NAME", "deer-flow"),
    )

    log_file = os.environ.get("DEER_FLOW_DECISION_LOG_FILE", "").strip()
    decision_log = DecisionLogConfig(
        enabled=True,
        output="file" if log_file else "stdout",
        file_path=log_file,
    )

    metrics_enabled = os.environ.get("DEER_FLOW_METRICS_ENABLED", "true").strip().lower() != "false"

    _observability_config = ObservabilityConfig(
        otel=otel,
        decision_log=decision_log,
        metrics_enabled=metrics_enabled,
    )
    return _observability_config


def reset_observability_config() -> None:
    """Reset cached config — for testing."""
    global _observability_config
    _observability_config = None
