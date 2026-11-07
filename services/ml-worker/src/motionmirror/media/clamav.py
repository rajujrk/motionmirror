"""Virus scanning for uploads, for the public-deployment security profile.

A pass doesn't make a file trustworthy, so media validation and sandboxing still apply. It fails closed: when scanning
is configured and the scanner can't answer, the upload stays quarantined and the job retries.
"""

from __future__ import annotations

import socket
import struct
from pathlib import Path

CHUNK_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 300
MAX_REPLY_BYTES = 4096


class ScannerUnavailable(RuntimeError):
    """The scanner couldn't give an answer, which is not the same as a clean result."""


def scan(path: Path, host: str, port: int = 3310, timeout: float = TIMEOUT_SECONDS) -> str | None:
    """Streams the file to clamd. Returns the signature it found, or None when clean."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection, path.open("rb") as stream:
            connection.sendall(b"zINSTREAM\0")
            while chunk := stream.read(CHUNK_BYTES):
                connection.sendall(struct.pack("!L", len(chunk)) + chunk)
            connection.sendall(struct.pack("!L", 0))
            reply = _read_reply(connection)
    except OSError as error:
        raise ScannerUnavailable(type(error).__name__) from error
    return _parse(reply)


def _read_reply(connection: socket.socket) -> str:
    reply = b""
    while b"\0" not in reply and len(reply) < MAX_REPLY_BYTES:
        part = connection.recv(1024)
        if not part:
            break
        reply += part
    return reply.split(b"\0", 1)[0].decode(errors="replace").strip()


def _parse(reply: str) -> str | None:
    """Anything but a definite answer is treated as no answer, including clamd's own size-limit error."""
    if reply.endswith(" OK"):
        return None
    if reply.endswith(" FOUND"):
        return reply.removesuffix(" FOUND").split(":", 1)[-1].strip()
    raise ScannerUnavailable(f"unexpected reply: {reply[:120]}")
