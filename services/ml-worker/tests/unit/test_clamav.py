"""Against a fake clamd that speaks the real protocol, so the framing and every outcome can be checked."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from motionmirror.media.clamav import ScannerUnavailable, scan
from motionmirror.worker.child import scan_upload
from motionmirror.worker.failures import AttemptFailure


class FakeClamd:
    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.received = b""
        self.command = b""
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read(self, connection: socket.socket, count: int) -> bytes:
        data = b""
        while len(data) < count:
            part = connection.recv(count - len(data))
            if not part:
                return data
            data += part
        return data

    def _serve(self) -> None:
        connection, _ = self.server.accept()
        with connection:
            self.command = self._read(connection, len(b"zINSTREAM\0"))
            while True:
                size = struct.unpack("!L", self._read(connection, 4))[0]
                if size == 0:
                    break
                self.received += self._read(connection, size)
            connection.sendall(self.reply)


@pytest.fixture
def upload(tmp_path: Path) -> Path:
    path = tmp_path / "upload.bin"
    path.write_bytes(b"x" * (3 * 1024 * 1024 + 17))
    return path


@pytest.fixture
def clean() -> Iterator[FakeClamd]:
    yield FakeClamd(b"stream: OK\0")


def test_a_clean_file_passes_and_every_byte_reached_the_scanner(upload: Path, clean: FakeClamd) -> None:
    assert scan(upload, "127.0.0.1", clean.port) is None
    clean.thread.join(5)
    assert clean.command == b"zINSTREAM\0"
    assert clean.received == upload.read_bytes()


def test_a_detection_returns_its_signature(upload: Path) -> None:
    clamd = FakeClamd(b"stream: Eicar-Test-Signature FOUND\0")
    assert scan(upload, "127.0.0.1", clamd.port) == "Eicar-Test-Signature"


def test_clamds_size_limit_error_is_not_a_clean_result(upload: Path) -> None:
    clamd = FakeClamd(b"INSTREAM size limit exceeded. ERROR\0")
    with pytest.raises(ScannerUnavailable, match="size limit"):
        scan(upload, "127.0.0.1", clamd.port)


def test_an_unreachable_scanner_is_not_a_clean_result(upload: Path) -> None:
    with socket.create_server(("127.0.0.1", 0)) as unused:
        port = unused.getsockname()[1]
    with pytest.raises(ScannerUnavailable):
        scan(upload, "127.0.0.1", port, timeout=1)


def test_scanning_is_skipped_only_when_it_isnt_configured(upload: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MM_CLAMD_HOST", raising=False)
    scan_upload(upload)


def test_an_infected_upload_is_rejected_without_a_retry(upload: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clamd = FakeClamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
    monkeypatch.setenv("MM_CLAMD_HOST", "127.0.0.1")
    monkeypatch.setenv("MM_CLAMD_PORT", str(clamd.port))
    with pytest.raises(AttemptFailure) as failure:
        scan_upload(upload)
    assert failure.value.code == "INVALID_MEDIA"
    assert failure.value.retryable is False
    assert "EICAR" not in failure.value.detail, "the signature is for the operator, not the uploader"


def test_a_scanner_outage_keeps_the_upload_quarantined_and_retries(
    upload: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.create_server(("127.0.0.1", 0)) as unused:
        port = unused.getsockname()[1]
    monkeypatch.setenv("MM_CLAMD_HOST", "127.0.0.1")
    monkeypatch.setenv("MM_CLAMD_PORT", str(port))
    with pytest.raises(AttemptFailure) as failure:
        scan_upload(upload)
    assert failure.value.code == "SCANNER_UNAVAILABLE"
    assert failure.value.retryable is True
