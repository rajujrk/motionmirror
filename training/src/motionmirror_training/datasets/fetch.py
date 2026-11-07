"""Explicit, resumable, checksummed dataset downloads. Never called from bootstrap or tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import httpx

CHUNK_BYTES = 1024 * 1024
DISK_MARGIN_BYTES = 5 * 1024**3


class FetchError(RuntimeError):
    """The download did not complete."""


class InsufficientDiskError(FetchError):
    """There isn't enough free disk for the download."""


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def download_resumable(
    url: str,
    target: Path,
    expected_bytes: int | None = None,
    auth: tuple[str, str] | None = None,
    retries: int = 5,
    free_bytes: Callable[[Path], int] = _free_bytes,
    on_progress: Callable[[int], None] | None = None,
) -> Path:
    """Download to `<target>.part`, continuing from its current size, then rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.part")
    done = part.stat().st_size if part.exists() else 0

    if expected_bytes is not None and free_bytes(target.parent) < expected_bytes - done:
        raise InsufficientDiskError(f"{target.parent} has too little free space for {target.name}")

    attempt = 0
    while True:
        try:
            _download_once(url, part, auth, on_progress)
            break
        except (httpx.HTTPError, OSError) as error:
            attempt += 1
            if attempt > retries:
                raise FetchError(f"download of {target.name} failed: {error}") from error
            time.sleep(min(2**attempt, 60))

    if expected_bytes is not None and part.stat().st_size != expected_bytes:
        raise FetchError(f"{target.name}: got {part.stat().st_size}, expected {expected_bytes}")
    part.rename(target)
    return target


def _download_once(
    url: str,
    part: Path,
    auth: tuple[str, str] | None,
    on_progress: Callable[[int], None] | None,
) -> None:
    done = part.stat().st_size if part.exists() else 0
    headers = {"Accept-Encoding": "identity"}
    if done:
        headers["Range"] = f"bytes={done}-"
    with (
        httpx.Client(
            follow_redirects=True, timeout=httpx.Timeout(60, read=300), auth=auth
        ) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.status_code == 416:
            return
        response.raise_for_status()
        if done and response.status_code != 206:
            raise FetchError("the server ignored the range request, refusing to restart silently")
        with part.open("ab") as output:
            # iter_raw keeps every received byte, so an interruption loses nothing.
            for chunk in response.iter_raw():
                output.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_files(root: Path, manifest: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p != manifest)


def write_manifest(
    root: Path, manifest: Path, dataset_id: str, version: str | None, retrieval_date: str
) -> None:
    files = {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in _dataset_files(root, manifest)
    }
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "version": version,
                "retrieval_date": retrieval_date,
                "files": files,
            },
            indent=2,
        )
    )


def verify_manifest(root: Path, manifest: Path) -> list[str]:
    """Return the relative paths that are missing or don't match their recorded hash."""
    recorded = json.loads(manifest.read_text())["files"]
    return [
        name
        for name, info in recorded.items()
        if not (root / name).is_file() or sha256_file(root / name) != info["sha256"]
    ]
