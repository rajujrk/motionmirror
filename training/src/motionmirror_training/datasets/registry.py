"""Dataset registry and licence gate."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


class DatasetBlockedError(RuntimeError):
    """The licence gate refused this dataset."""


class Purpose(StrEnum):
    RESEARCH = "research"
    PRODUCTION = "production"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Review(_Strict):
    status: str  # approved | blocked
    scope: str  # research_only | production | none
    reviewer: str | None
    review_date: str | None
    decision: str

    @model_validator(mode="after")
    def approved_review_names_a_reviewer(self) -> Review:
        if self.status == "approved" and not (self.reviewer and self.review_date):
            raise ValueError("an approved review needs a reviewer and a review_date")
        return self


class DatasetEntry(_Strict):
    id: str
    name: str
    version: str | None
    use: str
    primary_url: str
    fallback_url: str | None
    access: str  # kaggle | blocked
    source_ref: str | None
    license_name: str
    license_url: str | None
    license_notes: str
    commercial_use_ok: bool = False
    transfer_requires_permission: bool = False
    attribution: str
    consent_notes: str
    size_bytes: int | None
    checksum_manifest: str | None
    retrieval_date: str | None
    review: Review


class RejectedDataset(_Strict):
    id: str
    reason: str


class _RegistryFile(_Strict):
    schema_version: int
    datasets: list[DatasetEntry]
    rejected: list[RejectedDataset]


class Registry:
    def __init__(self, parsed: _RegistryFile) -> None:
        self.datasets = {entry.id: entry for entry in parsed.datasets}
        self.rejected = {entry.id: entry for entry in parsed.rejected}

    def get(self, dataset_id: str) -> DatasetEntry:
        if dataset_id not in self.datasets:
            raise KeyError(f"unknown dataset {dataset_id!r}")
        return self.datasets[dataset_id]


def load_registry(path: Path) -> Registry:
    return Registry(_RegistryFile.model_validate(yaml.safe_load(path.read_text())))


def assert_fetch_allowed(entry: DatasetEntry) -> None:
    if entry.review.status != "approved" or entry.access == "blocked":
        raise DatasetBlockedError(
            f"{entry.id} is blocked: {entry.review.decision} Licence: {entry.license_name}."
        )


def assert_training_allowed(entry: DatasetEntry, purpose: Purpose) -> None:
    assert_fetch_allowed(entry)
    if purpose is Purpose.PRODUCTION and not (
        entry.commercial_use_ok and entry.review.scope == "production"
    ):
        raise DatasetBlockedError(
            f"{entry.id} cannot be used for production training: reviewed as "
            f"{entry.review.scope}, licence {entry.license_name}. "
            "It can only produce a labelled research adapter."
        )
