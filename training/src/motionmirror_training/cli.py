"""Training track command line. `make` targets call this."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from motionmirror_training.datasets.fetch import (
    download_resumable,
    verify_manifest,
    write_manifest,
)
from motionmirror_training.datasets.registry import (
    DatasetBlockedError,
    assert_fetch_allowed,
    load_registry,
)
from motionmirror_training.datasets.sources import kaggle_credentials, kaggle_url

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "configs" / "datasets" / "registry.yaml"


def dataset_root() -> Path:
    return Path(os.environ.get("MM_DATASET_ROOT", REPO_ROOT / ".data" / "datasets"))


def _progress(total: int | None):
    step = {"next": 0}

    def report(done: int) -> None:
        if done >= step["next"]:
            share = f" ({done / total:.1%})" if total else ""
            print(f"downloaded {done / 1024**3:.2f} GB{share}", flush=True)
            step["next"] = done + 1024**3

    return report


def datasets_fetch(dataset_id: str) -> int:
    entry = load_registry(REGISTRY_PATH).get(dataset_id)
    assert_fetch_allowed(entry)
    if entry.access != "kaggle" or not entry.source_ref:
        raise DatasetBlockedError(f"{dataset_id}: no automatic source is configured")

    root = dataset_root() / dataset_id
    target = root / "download" / f"{dataset_id}.zip"
    if target.exists():
        print(f"already downloaded: {target}")
        return 0

    print(f"{entry.name}\nlicence: {entry.license_name}\nreview: {entry.review.decision}\n", flush=True)
    download_resumable(
        kaggle_url(entry.source_ref),
        target,
        auth=kaggle_credentials(),
        on_progress=_progress(entry.size_bytes),
    )
    print(f"downloaded: {target}")
    return 0


def datasets_validate(dataset_id: str) -> int:
    entry = load_registry(REGISTRY_PATH).get(dataset_id)
    assert_fetch_allowed(entry)
    root = dataset_root() / dataset_id
    manifest = root / "MANIFEST.json"
    if not manifest.exists():
        if not any(root.rglob("*")):
            print(f"{dataset_id}: nothing downloaded under {root}", file=sys.stderr)
            return 1
        write_manifest(root, manifest, dataset_id, entry.version, dt.date.today().isoformat())
        print(f"wrote {manifest}")
    failures = verify_manifest(root, manifest)
    for name in failures:
        print(f"checksum mismatch or missing: {name}", file=sys.stderr)
    print(f"{dataset_id}: {'FAILED' if failures else 'OK'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mm-train")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("datasets-fetch")
    fetch.add_argument("--dataset", required=True)
    validate = commands.add_parser("datasets-validate")
    validate.add_argument("--dataset", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "datasets-fetch":
            return datasets_fetch(args.dataset)
        return datasets_validate(args.dataset)
    except DatasetBlockedError as blocked:
        print(f"licence gate: {blocked}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
