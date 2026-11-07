import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from motionmirror_training.datasets.fetch import (
    FetchError,
    InsufficientDiskError,
    download_resumable,
    verify_manifest,
    write_manifest,
)

PAYLOAD = bytes(range(256)) * 2000


class _Handler(BaseHTTPRequestHandler):
    fail_after: int | None = None
    range_requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        start = 0
        header = self.headers.get("Range")
        if header:
            _Handler.range_requests.append(header)
            start = int(header.removeprefix("bytes=").split("-")[0])
        body = PAYLOAD[start:]
        self.send_response(206 if header else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        limit = _Handler.fail_after
        if limit is not None:
            _Handler.fail_after = None
            self.wfile.write(body[:limit])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server():
    _Handler.fail_after = None
    _Handler.range_requests = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/data.zip"
    httpd.shutdown()


def test_download_writes_the_whole_file(server: str, tmp_path: Path) -> None:
    target = tmp_path / "data.zip"
    download_resumable(server, target, expected_bytes=len(PAYLOAD))
    assert target.read_bytes() == PAYLOAD
    assert not (tmp_path / "data.zip.part").exists()


def test_interrupted_download_resumes_with_a_range_request(server: str, tmp_path: Path) -> None:
    target = tmp_path / "data.zip"
    _Handler.fail_after = 100_000
    with pytest.raises(FetchError):
        download_resumable(server, target, expected_bytes=len(PAYLOAD), retries=0)
    assert (tmp_path / "data.zip.part").stat().st_size == 100_000

    download_resumable(server, target, expected_bytes=len(PAYLOAD))
    assert target.read_bytes() == PAYLOAD
    assert _Handler.range_requests == ["bytes=100000-"]


def test_download_refuses_when_the_disk_is_too_small(server: str, tmp_path: Path) -> None:
    with pytest.raises(InsufficientDiskError):
        download_resumable(
            server, tmp_path / "data.zip", expected_bytes=len(PAYLOAD), free_bytes=lambda _: 10
        )


def test_manifest_records_real_hashes_and_detects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "a.bin").write_bytes(b"hello")
    manifest = root / "MANIFEST.json"

    write_manifest(root, manifest, dataset_id="demo", version="1", retrieval_date="2026-11-06")

    recorded = json.loads(manifest.read_text())
    assert recorded["files"]["a.bin"]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert verify_manifest(root, manifest) == []

    (root / "a.bin").write_bytes(b"changed")
    assert verify_manifest(root, manifest) == ["a.bin"]


def test_the_manifest_ignores_hidden_tool_folders(tmp_path: Path) -> None:
    root = tmp_path / "download"
    (root / ".tool-state").mkdir(parents=True)
    (root / "a.zip").write_bytes(b"archive")
    (root / ".tool-state" / "cache.json").write_text("{}")
    manifest = tmp_path / "MANIFEST.json"

    write_manifest(root, manifest, dataset_id="demo", version="1", retrieval_date="2026-11-07")

    assert list(json.loads(manifest.read_text())["files"]) == ["a.zip"]


def test_validation_checks_the_download_not_what_was_built_from_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from motionmirror_training import cli

    root = tmp_path / "tiktok-dance"
    (root / "download").mkdir(parents=True)
    (root / "built").mkdir()
    (root / "download" / "tiktok-dance.zip").write_bytes(b"archive")
    (root / "built" / "manifest.json").write_text("first build")
    monkeypatch.setenv("MM_DATASET_ROOT", str(tmp_path))

    assert cli.datasets_validate("tiktok-dance") == 0
    recorded = json.loads((root / "MANIFEST.json").read_text())["files"]
    assert list(recorded) == ["tiktok-dance.zip"]

    (root / "built" / "manifest.json").write_text("a rebuild")
    assert cli.datasets_validate("tiktok-dance") == 0

    (root / "download" / "tiktok-dance.zip").write_bytes(b"corrupted")
    assert cli.datasets_validate("tiktok-dance") == 1
    assert "tiktok-dance.zip" in capsys.readouterr().err


def test_validation_waits_for_an_unfinished_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A .part file is renamed when the download finishes, so a manifest written now would list a file that vanishes."""
    from motionmirror_training import cli

    root = tmp_path / "tiktok-dance"
    (root / "download").mkdir(parents=True)
    (root / "download" / "tiktok-dance.zip.part").write_bytes(b"half")
    monkeypatch.setenv("MM_DATASET_ROOT", str(tmp_path))

    assert cli.datasets_validate("tiktok-dance") == 1
    assert "hasn't finished" in capsys.readouterr().err
    assert not (root / "MANIFEST.json").exists()
