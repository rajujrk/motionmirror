from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from motionmirror.worker.telemetry import attempt_span, record_stage
from tests.unit.test_runner import LEASE, FakeApi, child, runner

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
PARENT = "b7ad6b7169203331"


@pytest.fixture
def traced() -> tuple[object, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_an_attempt_joins_the_trace_the_api_started(traced: tuple[object, InMemorySpanExporter]) -> None:
    tracer, exporter = traced
    context = {"traceparent": f"00-{TRACE_ID}-{PARENT}-01"}
    with attempt_span(context, "job-1", "generate", "mock", tracer=tracer):  # type: ignore[arg-type]
        pass
    span = exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") == TRACE_ID
    assert format(span.parent.span_id, "016x") == PARENT


def test_a_message_with_no_trace_still_gets_a_span(traced: tuple[object, InMemorySpanExporter]) -> None:
    tracer, exporter = traced
    with attempt_span(None, "job-1", "generate", "mock", tracer=tracer):  # type: ignore[arg-type]
        pass
    assert exporter.get_finished_spans()[0].parent is None


def test_spans_hold_only_a_short_list_of_safe_attributes(traced: tuple[object, InMemorySpanExporter]) -> None:
    tracer, exporter = traced
    with attempt_span(None, "job-1", "generate", "mock", tracer=tracer) as span:  # type: ignore[arg-type]
        span.set_attribute("mm.outcome", "SUCCEEDED")
        record_stage("encode")
    finished = exporter.get_finished_spans()[0]
    assert set(finished.attributes) == {"mm.job_id", "mm.task_type", "mm.backend_id", "mm.outcome"}
    assert finished.events[0].attributes["mm.stage"] == "encode"


def test_recording_a_stage_with_no_tracing_set_up_is_harmless() -> None:
    record_stage("encode")


@pytest.mark.asyncio
async def test_a_failed_attempts_logs_hold_no_urls_tokens_or_prompts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The child's failure detail and the signed URLs pass through the runner, so this is where a leak would start."""
    secret_prompt = "a very private prompt"
    lease = type(LEASE)(
        "job",
        "attempt-1",
        1,
        1,
        {"kind": "PREVIEW", "backend_id": "mock", "inputs": {}, "settings": {"positive_prompt": secret_prompt}},
    )
    api = FakeApi()
    body = """
failure = {"ok": False, "code": "INTERNAL_ERROR", "detail": "boom", "retryable": True}
(workspace / "attempt_result.json").write_text(json.dumps(failure))
"""
    with caplog.at_level(logging.DEBUG):
        await runner(api, tmp_path, child(tmp_path, body)).run(lease)
    logged = caplog.text + json.dumps([str(call) for call in api.calls])
    assert secret_prompt not in logged
    assert "https://read/" not in caplog.text
    assert "Bearer" not in caplog.text
