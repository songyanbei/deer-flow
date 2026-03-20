"""One-time observability initialization — called at application startup."""

import logging
import os
import sys

logger = logging.getLogger(__name__)


def init_observability() -> None:
    """Initialize the observability subsystem.

    - Configures the dedicated decision logger.
    - Optionally initializes OpenTelemetry exporters.
    """
    _setup_decision_logger()
    _setup_otel_if_enabled()
    logger.info("[Observability] Initialization complete.")


def _setup_decision_logger() -> None:
    """Configure the ``deer-flow.decisions`` logger.

    - Output is pure JSON (no timestamp/level prefix from the formatter).
    - ``propagate = False`` ensures decision logs do not mix with module logs.
    - Target is stdout by default, or a file if ``DEER_FLOW_DECISION_LOG_FILE`` is set.
    """
    decision_logger = logging.getLogger("deer-flow.decisions")
    decision_logger.setLevel(logging.INFO)
    decision_logger.propagate = False

    # Avoid adding duplicate handlers on re-init
    if decision_logger.handlers:
        return

    log_file = os.environ.get("DEER_FLOW_DECISION_LOG_FILE", "").strip()
    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(logging.Formatter("%(message)s"))
    decision_logger.addHandler(handler)


def _setup_otel_if_enabled() -> None:
    """Initialize OpenTelemetry SDK if ``OTEL_ENABLED=true``."""
    otel_enabled = os.environ.get("OTEL_ENABLED", "false").strip().lower()
    if otel_enabled != "true":
        logger.info("[Observability] OpenTelemetry export disabled (OTEL_ENABLED=%s).", otel_enabled)
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        service_name = os.environ.get("OTEL_SERVICE_NAME", "deer-flow")
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(tracer_provider)

        logger.info("[Observability] OpenTelemetry tracer configured: service=%s endpoint=%s", service_name, endpoint)

        # Meter provider
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry import metrics as otel_metrics

            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint, insecure=True),
                export_interval_millis=15_000,
            )
            meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
            otel_metrics.set_meter_provider(meter_provider)
            logger.info("[Observability] OpenTelemetry meter configured.")
        except ImportError:
            logger.warning("[Observability] OTel metrics exporter not available, skipping meter setup.")

    except ImportError:
        logger.warning("[Observability] OpenTelemetry packages not installed, running without trace export.")
    except Exception as exc:
        logger.error("[Observability] Failed to initialize OpenTelemetry: %s", exc)
