"""Versioned contracts shared with the API. Schemas live in packages/contracts."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_REPO_CONTRACTS = Path(__file__).resolve().parents[5] / "packages" / "contracts"


def contracts_dir() -> Path:
    return Path(os.environ.get("MM_CONTRACTS_DIR", _REPO_CONTRACTS))


@lru_cache
def load_validator(relative_path: str) -> Draft202012Validator:
    schema: dict[str, Any] = json.loads((contracts_dir() / relative_path).read_text())
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
