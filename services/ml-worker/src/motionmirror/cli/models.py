"""make models-download: explicit, resumable and revision-pinned. Never runs during bootstrap, tests or startup."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
CONFIG_PATH = REPO_ROOT / "configs" / "models" / "wan-animate-14b.yaml"
DISK_MARGIN_BYTES = 5 * 1024**3


class InsufficientDisk(RuntimeError):
    """Not enough free space for this download."""


class IntegrityError(RuntimeError):
    """A downloaded file doesn't match the hub's recorded hash."""


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    sha256: str | None


def cache_dir() -> Path:
    return Path(os.environ.get("MM_MODEL_CACHE", REPO_ROOT / ".models"))


def load_profile(name: str, config_path: Path = CONFIG_PATH) -> list[dict[str, Any]]:
    config = yaml.safe_load(config_path.read_text())
    if name not in config["profiles"]:
        raise KeyError(f"Unknown profile {name!r}. Choose from: {', '.join(sorted(config['profiles']))}")
    return [{"id": artifact, **config["artifacts"][artifact]} for artifact in config["profiles"][name]]


def selected(files: list[RemoteFile], patterns: list[str]) -> list[RemoteFile]:
    return [item for item in files if any(fnmatch.fnmatch(item.path, pattern) for pattern in patterns)]


def check_disk(needed_bytes: int, target: Path, free_bytes: Callable[[Path], int] | None = None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    free = (free_bytes or (lambda path: shutil.disk_usage(path).free))(target)
    if free < needed_bytes + DISK_MARGIN_BYTES:
        raise InsufficientDisk(
            f"This needs {needed_bytes / 1e9:.1f} GB plus a safety margin, but {target} has {free / 1e9:.1f} GB free"
        )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: Path, files: list[RemoteFile]) -> dict[str, dict[str, Any]]:
    """Hash every large file against the hub's metadata. Small files have no LFS hash, so size is checked."""
    recorded: dict[str, dict[str, Any]] = {}
    for item in files:
        path = root / item.path
        if not path.is_file() or path.stat().st_size != item.size:
            raise IntegrityError(f"{item.path} is missing or the wrong size")
        actual = sha256_of(path)
        if item.sha256 is not None and actual != item.sha256:
            raise IntegrityError(f"{item.path} does not match the hub's recorded hash")
        recorded[item.path] = {"bytes": item.size, "sha256": actual}
    return recorded


def list_remote(repo: str, revision: str) -> list[RemoteFile]:
    from huggingface_hub import HfApi

    info = HfApi().repo_info(repo, revision=revision, files_metadata=True)
    return [
        RemoteFile(sibling.rfilename, sibling.size or 0, sibling.lfs.sha256 if sibling.lfs else None)
        for sibling in info.siblings
    ]


def download_profile(name: str) -> Path:
    from huggingface_hub import snapshot_download

    cache = cache_dir()
    artifacts = load_profile(name)
    plans = []
    for artifact in artifacts:
        files = selected(list_remote(artifact["repo"], artifact["revision"]), artifact["include"])
        if not files:
            raise IntegrityError(f"{artifact['id']} matched no files at {artifact['revision']}")
        plans.append((artifact, files))

    needed = 0
    for artifact, files in plans:
        root = cache / artifact["local_dir"]
        needed += sum(item.size for item in files if not (root / item.path).is_file())
    check_disk(needed, cache)
    print(f"Profile {name}: {needed / 1e9:.1f} GB to download into {cache}", flush=True)

    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"artifacts": {}}
    for artifact, files in plans:
        root = cache / artifact["local_dir"]
        print(f"Downloading {artifact['id']} at {artifact['revision'][:10]}", flush=True)
        snapshot_download(
            artifact["repo"], revision=artifact["revision"], allow_patterns=artifact["include"], local_dir=root
        )
        print(f"Verifying {artifact['id']}", flush=True)
        manifest["artifacts"][artifact["id"]] = {
            "repo": artifact["repo"],
            "revision": artifact["revision"],
            "license": artifact["license"],
            "files": verify(root, files),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Done. Manifest: {manifest_path}")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mm-models")
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    try:
        download_profile(args.profile)
    except (KeyError, InsufficientDisk, IntegrityError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
