"""Offline archive admission tests; no native runtime is executed."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "fetch_acceptance_runtime", Path(__file__).resolve().parents[1] / "scripts" / "fetch_acceptance_runtime.py"
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
BINARY = b"synthetic archive fixture; never executed"


def archive_fixture(tmp_path, monkeypatch, *, extra=None, duplicate=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, data in [(runtime.CLI_MEMBER, BINARY), (runtime.RUNTIME_DIRECTORY + "/LICENSE", b"fixture license")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        if duplicate:
            info = tarfile.TarInfo(runtime.CLI_MEMBER)
            info.size = len(BINARY)
            bundle.addfile(info, io.BytesIO(BINARY))
        if extra is not None:
            bundle.addfile(extra)
    data = buffer.getvalue()
    path = tmp_path / runtime.ARCHIVE_NAME
    path.write_bytes(data)
    monkeypatch.setattr(runtime, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(runtime, "CLI_SHA256", hashlib.sha256(BINARY).hexdigest())
    return path


def test_only_verified_cli_is_extracted(tmp_path, monkeypatch):
    archive = archive_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "extracted" / "pdfcraft-cli"
    assert runtime.extract_cli(archive, destination) == destination
    assert destination.read_bytes() == BINARY
    assert [p.name for p in destination.parent.iterdir()] == ["pdfcraft-cli"]


def test_archive_hash_is_checked_before_interpretation(tmp_path):
    archive = tmp_path / "untrusted.tar.gz"
    archive.write_bytes(b"not an archive")
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="archive SHA-256"):
        runtime.extract_cli(archive, destination)
    assert not destination.exists()


def test_executable_hash_mismatch_publishes_nothing(tmp_path, monkeypatch):
    archive = archive_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "CLI_SHA256", "0" * 64)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="executable SHA-256"):
        runtime.extract_cli(archive, destination)
    assert not destination.exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "other-root/file", "{root}/../escape", "{root}/bad\\path"])
def test_unexpected_paths_are_rejected_before_output(tmp_path, monkeypatch, name):
    member = tarfile.TarInfo(name.format(root=runtime.RUNTIME_DIRECTORY))
    archive = archive_fixture(tmp_path, monkeypatch, extra=member)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="archive path"):
        runtime.extract_cli(archive, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_links_and_special_members_are_rejected(tmp_path, monkeypatch, kind):
    member = tarfile.TarInfo(runtime.RUNTIME_DIRECTORY + "/not-regular")
    member.type = kind
    member.linkname = runtime.CLI_MEMBER
    archive = archive_fixture(tmp_path, monkeypatch, extra=member)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="links/special"):
        runtime.extract_cli(archive, destination)
    assert not destination.exists()


def test_duplicate_members_are_rejected(tmp_path, monkeypatch):
    archive = archive_fixture(tmp_path, monkeypatch, duplicate=True)
    with pytest.raises(ValueError, match="duplicate"):
        runtime.extract_cli(archive, tmp_path / "must-not-exist")


def test_existing_different_file_is_never_overwritten(tmp_path, monkeypatch):
    archive = archive_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "existing"
    destination.write_bytes(b"keep original bytes")
    with pytest.raises(ValueError, match="existing CLI destination"):
        runtime.extract_cli(archive, destination)
    assert destination.read_bytes() == b"keep original bytes"


def test_verified_existing_file_can_be_reused_without_rewrite(tmp_path, monkeypatch):
    archive = archive_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "existing"
    destination.write_bytes(BINARY)
    before = destination.stat().st_mtime_ns
    runtime.extract_cli(archive, destination)
    assert destination.stat().st_mtime_ns == before


def test_verified_existing_archive_needs_no_network(tmp_path, monkeypatch):
    archive_fixture(tmp_path, monkeypatch)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("offline fixture must not use the network")

    monkeypatch.setattr(runtime, "urlopen", forbidden_network)
    destination = runtime.fetch_runtime(tmp_path)
    assert destination.read_bytes() == BINARY


def test_bad_download_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "urlopen", lambda *args, **kwargs: io.BytesIO(b"unexpected download"))
    with pytest.raises(ValueError, match="download size/hash"):
        runtime.fetch_runtime(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_oversized_archive_is_rejected(tmp_path, monkeypatch):
    archive = archive_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "MAX_ARCHIVE", 1)
    with pytest.raises(ValueError, match="bounded regular"):
        runtime.extract_cli(archive, tmp_path / "must-not-exist")
