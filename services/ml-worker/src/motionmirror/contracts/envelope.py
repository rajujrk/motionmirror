"""The RabbitMQ task message. Identifiers only; the real request comes from the API after acquiring a lease."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, ValidationError

from motionmirror.contracts import load_validator

SCHEMA = "messages/task-envelope.v1.schema.json"


class InvalidEnvelope(ValueError):
    """Malformed or unsupported. The consumer rejects these without requeue."""


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    event_id: str
    job_id: str
    dispatch_generation: int
    task_type: str
    backend_id: str
    config_revision: str
    created_at: str
    trace: dict[str, str] | None = None


def parse_envelope(body: bytes) -> TaskEnvelope:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidEnvelope("message is not JSON") from error
    problems = sorted(load_validator(SCHEMA).iter_errors(data), key=str)
    if problems:
        raise InvalidEnvelope(f"message breaks the v1 schema at {list(problems[0].path)}")
    try:
        return TaskEnvelope.model_validate(data)
    except ValidationError as error:
        raise InvalidEnvelope("message does not match the envelope") from error
