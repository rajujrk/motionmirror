from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from motionmirror.cli.models import (
    CONFIG_PATH,
    InsufficientDisk,
    IntegrityError,
    RemoteFile,
    check_disk,
    load_profile,
    selected,
    verify,
)


def test_every_profile_resolves_to_pinned_full_revisions() -> None:
    for name in ("preprocess", "preprocess-gpu", "wan", "mac-spike"):
        for artifact in load_profile(name):
            assert len(artifact["revision"]) == 40, f"{artifact['id']} must pin a commit"
            assert artifact["license"]


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(KeyError, match="Unknown profile"):
        load_profile("anything-goes")


def test_the_config_never_downloads_the_agpl_detector_or_pickles_for_tracking() -> None:
    text = CONFIG_PATH.read_text()
    assert "yolov10" not in text
    for artifact in load_profile("preprocess"):
        assert not any(pattern.endswith(".pt") for pattern in artifact["include"])


def test_include_patterns_select_only_what_was_asked_for() -> None:
    files = [RemoteFile("a/model.safetensors", 1, None), RemoteFile("b/other.bin", 1, None)]
    assert [item.path for item in selected(files, ["a/*"])] == ["a/model.safetensors"]


def test_too_little_disk_is_refused_before_any_download(tmp_path: Path) -> None:
    with pytest.raises(InsufficientDisk, match="GB"):
        check_disk(100 * 10**9, tmp_path, free_bytes=lambda _: 10**9)


def test_a_file_matching_the_hub_hash_passes(tmp_path: Path) -> None:
    (tmp_path / "weights.bin").write_bytes(b"weights")
    digest = hashlib.sha256(b"weights").hexdigest()
    recorded = verify(tmp_path, [RemoteFile("weights.bin", 7, digest)])
    assert recorded["weights.bin"]["sha256"] == digest


def test_a_tampered_file_fails_verification(tmp_path: Path) -> None:
    (tmp_path / "weights.bin").write_bytes(b"tampered")
    with pytest.raises(IntegrityError, match="recorded hash"):
        verify(tmp_path, [RemoteFile("weights.bin", 8, "0" * 64)])


def test_a_partial_file_fails_verification(tmp_path: Path) -> None:
    (tmp_path / "weights.bin").write_bytes(b"part")
    with pytest.raises(IntegrityError, match="wrong size"):
        verify(tmp_path, [RemoteFile("weights.bin", 100, None)])
