"""Traces for the worker. Off unless an endpoint is set. Spans carry ids and outcomes, never prompts, URLs or tokens."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

TRACER_NAME = "motionmirror.worker"


def configure_tracing(endpoint: str | None, service_name: str, exporter: SpanExporter | None = None) -> None:
    if not endpoint and exporter is None:
        return
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint)
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


@contextmanager
def attempt_span(
    context: dict[str, str] | None, job_id: str, task_type: str, backend_id: str, tracer: trace.Tracer | None = None
) -> Iterator[trace.Span]:
    """One span per attempt, joined to the trace the API started when the job was submitted."""
    parent = TraceContextTextMapPropagator().extract(context or {})
    active: Any = tracer or trace.get_tracer(TRACER_NAME)
    attributes = {"mm.job_id": job_id, "mm.task_type": task_type, "mm.backend_id": backend_id}
    with active.start_as_current_span("attempt", context=parent, attributes=attributes) as span:
        yield span


def record_stage(stage: str) -> None:
    trace.get_current_span().add_event("stage", {"mm.stage": stage})
