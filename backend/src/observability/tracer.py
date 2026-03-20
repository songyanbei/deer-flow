"""Span management facade — uses OpenTelemetry when available, noop otherwise."""

import contextlib
import time
from contextlib import contextmanager
from typing import Any, Generator


def _sanitize_attributes(attrs: dict[str, Any] | None) -> dict[str, Any]:
    """Convert non-primitive values to strings, truncate long strings."""
    if not attrs:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in attrs.items():
        if isinstance(value, (str, int, float, bool)):
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + "..."
            sanitized[key] = value
        elif value is None:
            continue
        else:
            text = str(value)
            if len(text) > 500:
                text = text[:500] + "..."
            sanitized[key] = text
    return sanitized


class SpanHandle:
    """Lightweight wrapper around an OTel span or noop."""

    def __init__(self, otel_span: Any = None) -> None:
        self._span = otel_span
        self._t0 = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        if self._span is not None:
            try:
                self._span.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))
            except Exception:
                pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        if self._span is not None:
            try:
                self._span.add_event(name, attributes=_sanitize_attributes(attributes))
            except Exception:
                pass

    def record_error(self, exc: BaseException) -> None:
        if self._span is not None:
            try:
                from opentelemetry.trace import StatusCode
                self._span.set_status(StatusCode.ERROR, str(exc))
                self._span.record_exception(exc)
            except Exception:
                pass


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Generator[None, None, None]:
        yield None


_tracer: Any = None


def get_tracer() -> Any:
    """Return the OTel tracer or a noop tracer."""
    global _tracer
    if _tracer is not None:
        return _tracer
    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer("deer-flow")
    except ImportError:
        _tracer = _NoopTracer()
    return _tracer


@contextmanager
def span(
    name: str,
    attributes: dict[str, Any] | None = None,
    parent_span: Any = None,
) -> Generator[SpanHandle, None, None]:
    """Context manager that creates a span (OTel or noop).

    Yields a SpanHandle for setting attributes, adding events, etc.
    On exception, records the error on the span and re-raises.
    """
    tracer = get_tracer()
    sanitized = _sanitize_attributes(attributes)

    if isinstance(tracer, _NoopTracer):
        handle = SpanHandle()
        try:
            yield handle
        except Exception as exc:
            handle.record_error(exc)
            raise
        return

    # OTel path
    with tracer.start_as_current_span(name, attributes=sanitized) as otel_span:
        handle = SpanHandle(otel_span)
        try:
            yield handle
        except Exception as exc:
            handle.record_error(exc)
            raise
