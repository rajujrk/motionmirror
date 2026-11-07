from pathlib import Path

import pytest

from motionmirror_training.datasets.registry import (
    DatasetBlockedError,
    Purpose,
    assert_fetch_allowed,
    assert_training_allowed,
    load_registry,
)

REGISTRY = Path(__file__).parents[2] / "configs/datasets/registry.yaml"


@pytest.fixture
def registry():
    return load_registry(REGISTRY)


def test_checked_in_registry_is_valid(registry) -> None:
    assert {"tiktok-dance", "ubc-fashion", "humanvid-pexels"} <= set(registry.datasets)


def test_every_dataset_defaults_to_no_commercial_use(registry) -> None:
    assert all(not entry.commercial_use_ok for entry in registry.datasets.values())


def test_unreviewed_dataset_cannot_be_fetched(registry) -> None:
    with pytest.raises(DatasetBlockedError, match="ubc-fashion"):
        assert_fetch_allowed(registry.get("ubc-fashion"))


def test_research_only_dataset_can_be_fetched(registry) -> None:
    assert_fetch_allowed(registry.get("tiktok-dance"))


def test_research_training_is_allowed_on_research_data(registry) -> None:
    assert_training_allowed(registry.get("tiktok-dance"), Purpose.RESEARCH)


def test_production_training_is_blocked_on_research_data(registry) -> None:
    with pytest.raises(DatasetBlockedError, match="production"):
        assert_training_allowed(registry.get("tiktok-dance"), Purpose.PRODUCTION)


def test_unknown_dataset_is_an_error(registry) -> None:
    with pytest.raises(KeyError):
        registry.get("not-a-dataset")


def test_registry_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("schema_version: 1\ndatasets: []\nrejected: []\nsurprise: true\n")
    with pytest.raises(ValueError):
        load_registry(path)


def test_reviewed_dataset_must_name_a_reviewer(tmp_path: Path) -> None:
    text = REGISTRY.read_text().replace("reviewer: Product owner", "reviewer: null")
    path = tmp_path / "registry.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match="reviewer"):
        load_registry(path)
