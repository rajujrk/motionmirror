"""Download sources for registry entries."""

from __future__ import annotations

import json
from pathlib import Path

KAGGLE_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/{ref}"


def kaggle_credentials(path: Path | None = None) -> tuple[str, str]:
    """Read the user's Kaggle token. The value is never logged or stored."""
    token = path or Path.home() / ".kaggle" / "kaggle.json"
    data = json.loads(token.read_text())
    return data["username"], data["key"]


def kaggle_url(source_ref: str) -> str:
    return KAGGLE_DOWNLOAD.format(ref=source_ref)
