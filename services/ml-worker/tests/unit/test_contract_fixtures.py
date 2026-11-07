"""The Java API runs these same fixtures, so both languages accept and reject the same inputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from motionmirror.contracts.envelope import InvalidEnvelope, parse_envelope
from motionmirror.contracts.settings import first_error

FIXTURES = Path(__file__).resolve().parents[4] / "packages" / "contracts" / "fixtures"


def files(directory: str) -> list[Path]:
    paths = sorted((FIXTURES / directory).glob("*.json"))
    assert paths
    return paths


@pytest.mark.parametrize("path", files("generation-settings/valid"), ids=lambda p: p.stem)
def test_valid_settings_are_accepted(path: Path) -> None:
    assert first_error(json.loads(path.read_text())) is None


@pytest.mark.parametrize("path", files("generation-settings/invalid"), ids=lambda p: p.stem)
def test_invalid_settings_get_the_shared_code(path: Path) -> None:
    fixture = json.loads(path.read_text())
    assert first_error(fixture["settings"]) == fixture["expected_error"]


@pytest.mark.parametrize("path", files("task-envelope/valid"), ids=lambda p: p.stem)
def test_valid_envelopes_parse(path: Path) -> None:
    envelope = parse_envelope(path.read_bytes())
    assert envelope.schema_version == 1


@pytest.mark.parametrize("path", files("task-envelope/invalid"), ids=lambda p: p.stem)
def test_invalid_envelopes_are_rejected(path: Path) -> None:
    with pytest.raises(InvalidEnvelope):
        parse_envelope(path.read_bytes())


def test_non_json_is_rejected() -> None:
    with pytest.raises(InvalidEnvelope):
        parse_envelope(b"not json")
