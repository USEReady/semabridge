"""
SemaBridge Telemetry Module
===========================

Provides a thin, optional wrapper around OpenTelemetry (OTLP) tracing and
metrics, plus a Snowflake-backed run-summary sink.

Design choices:
- ALL OpenTelemetry imports are **deferred** so the package remains importable
  even when ``opentelemetry-*`` packages are not installed.
- When telemetry is disabled or the packages are absent, every public function
  is a silent no-op.
- `configure_telemetry()` must be called once at process start (e.g. from the
  CLI entry-point or ExecutionEngine.execute()) before any spans are emitted.
"""

from __future__ import annotations

import functools
import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_tracer = None          # opentelemetry Tracer instance (or None)
_meter = None           # opentelemetry Meter instance (or None)
_run_counter = None     # opentelemetry Counter for pipeline runs
_step_histogram = None  # opentelemetry Histogram for step durations
_configured: bool = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def configure_telemetry(
    service_name: str = "semabridge",
    otlp_endpoint: Optional[str] = None,
    enabled: bool = True,
    insecure: bool = True,
) -> None:
    """
    Initialise OpenTelemetry tracing + metrics for the current process.

    Safe to call multiple times — only the first call has effect.

    Args:
        service_name:   OTel ``service.name`` resource attribute.
        otlp_endpoint:  gRPC OTLP collector endpoint
                        (e.g. ``http://localhost:4317``).
                        Pass ``None`` to use console exporter only.
        enabled:        Hard-disable all telemetry when False.
        insecure:       Skip TLS for the OTLP channel (dev / on-prem).
    """
    global _tracer, _meter, _run_counter, _step_histogram, _configured

    if _configured:
        return  # idempotent

    if not enabled:
        logger.debug("Telemetry disabled via configuration.")
        _configured = True
        return

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        resource = Resource.create({SERVICE_NAME: service_name})

        # ── Tracer ─────────────────────────────────────────────────────────
        tracer_provider = TracerProvider(resource=resource)

        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            span_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                insecure=insecure,
            )
        else:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            span_exporter = ConsoleSpanExporter()

        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer(service_name)

        # ── Meter ──────────────────────────────────────────────────────────
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            metric_exporter = OTLPMetricExporter(
                endpoint=otlp_endpoint,
                insecure=insecure,
            )
        else:
            from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
            metric_exporter = ConsoleMetricExporter()

        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30_000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        _meter = metrics.get_meter(service_name)

        # ── Instruments ────────────────────────────────────────────────────
        _run_counter = _meter.create_counter(
            "semabridge.pipeline.runs",
            unit="1",
            description="Total number of SemaBridge pipeline runs",
        )
        _step_histogram = _meter.create_histogram(
            "semabridge.pipeline.step_duration_seconds",
            unit="s",
            description="Duration of each pipeline step in seconds",
        )

        logger.info(
            f"OpenTelemetry configured — service={service_name}, "
            f"endpoint={otlp_endpoint or 'console'}"
        )

    except ImportError:
        logger.debug(
            "opentelemetry packages not installed — telemetry disabled. "
            "Install with: pip install opentelemetry-sdk "
            "opentelemetry-exporter-otlp-proto-grpc"
        )

    _configured = True


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------

@contextmanager
def pipeline_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> Generator[Any, None, None]:
    """
    Context manager that wraps a code block in an OTel span.

    No-op when telemetry is not configured.

    Usage::

        with pipeline_span("step6.convert_to_sml", {"source": "fabric"}):
            ...
    """
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, str(v))
        try:
            yield span
        except Exception as exc:
            try:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.ERROR, str(exc))
                span.record_exception(exc)
            except Exception:
                pass
            raise


@contextmanager
def step_span(
    step_number: int,
    step_name: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> Generator[Any, None, None]:
    """Convenience wrapper that instruments a pipeline step as a span + histogram."""
    combined = {"step_number": str(step_number), "step_name": step_name}
    if attributes:
        combined.update(attributes)

    start = time.monotonic()
    with pipeline_span(f"semabridge.step.{step_number}.{step_name}", combined) as span:
        try:
            yield span
        finally:
            elapsed = time.monotonic() - start
            if _step_histogram is not None:
                try:
                    _step_histogram.record(
                        elapsed,
                        {"step_number": str(step_number), "step_name": step_name},
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Counter helpers
# ---------------------------------------------------------------------------

def record_run(
    source: str,
    target: Optional[str],
    status: str,
) -> None:
    """Increment the pipeline run counter with outcome attributes."""
    if _run_counter is None:
        return
    try:
        _run_counter.add(
            1,
            {
                "source": source,
                "target": target or "none",
                "status": status,
            },
        )
    except Exception as exc:
        logger.debug(f"Telemetry record_run failed silently: {exc}")


# ---------------------------------------------------------------------------
# Flush helper (call at process exit or after a run)
# ---------------------------------------------------------------------------

def flush() -> None:
    """Force-flush pending spans and metrics to the exporter."""
    if not _configured:
        return
    try:
        from opentelemetry import trace, metrics
        tp = trace.get_tracer_provider()
        if hasattr(tp, "force_flush"):
            tp.force_flush(timeout_millis=5_000)
        mp = metrics.get_meter_provider()
        if hasattr(mp, "force_flush"):
            mp.force_flush(timeout_millis=5_000)
    except Exception as exc:
        logger.debug(f"Telemetry flush failed silently: {exc}")
