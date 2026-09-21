"""OpenTelemetry Telemetry & SigNoz Instrumentation for EdgeShield."""

import logging
import time
from typing import Optional
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger("edgeshield.telemetry")

# Global tracer and meter references
tracer = trace.get_tracer("edgeshield.tracer")
meter = metrics.get_meter("edgeshield.meter")

# Metric instruments
requests_total_counter = meter.create_counter(
    name="edgeshield_requests_total",
    description="Total number of HTTP requests processed by EdgeShield",
    unit="1",
)

ratelimit_blocked_counter = meter.create_counter(
    name="edgeshield_ratelimit_blocked_total",
    description="Total number of requests rejected due to rate limiting",
    unit="1",
)

redis_latency_histogram = meter.create_histogram(
    name="edgeshield_redis_latency_seconds",
    description="Duration of Redis rate limit pipeline execution in seconds",
    unit="s",
)

upstream_latency_histogram = meter.create_histogram(
    name="edgeshield_upstream_latency_seconds",
    description="Duration of upstream reverse proxy calls in seconds",
    unit="s",
)

fallback_counter = meter.create_counter(
    name="edgeshield_fallback_activations_total",
    description="Number of times in-memory fallback was triggered",
    unit="1",
)


def setup_telemetry(
    service_name: str = "edgeshield-gateway",
    endpoint: str = "http://signoz-otel-collector.signoz.svc.cluster.local:4318",
    environment: str = "production",
    enabled: bool = True,
) -> None:
    """
    Initializes the OpenTelemetry SDK with OTLP HTTP exporters pointing to SigNoz.
    """
    if not enabled or not endpoint:
        logger.info("OpenTelemetry telemetry is disabled or endpoint is empty.")
        return

    try:
        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": environment,
                "service.version": "0.1.0",
            }
        )

        # Traces setup
        trace_endpoint = endpoint if endpoint.endswith("/v1/traces") else f"{endpoint}/v1/traces"
        span_exporter = OTLPSpanExporter(endpoint=trace_endpoint)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)

        # Metrics setup
        metric_endpoint = endpoint if endpoint.endswith("/v1/metrics") else f"{endpoint}/v1/metrics"
        metric_exporter = OTLPMetricExporter(endpoint=metric_endpoint)
        metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)

        logger.info("Successfully initialized OpenTelemetry export to SigNoz at %s", endpoint)
    except Exception as exc:
        logger.warning("Failed to initialize OpenTelemetry SigNoz exporter: %s", exc)


def record_request_metric(tier: str, status_code: int, is_blocked: bool = False, is_fallback: bool = False) -> None:
    """Convenience helper to record standard request metrics."""
    attributes = {"tier": tier, "status_code": str(status_code)}
    requests_total_counter.add(1, attributes)
    if is_blocked:
        ratelimit_blocked_counter.add(1, attributes)
    if is_fallback:
        fallback_counter.add(1, attributes)
