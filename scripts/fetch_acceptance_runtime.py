"""Opt-in, hash-pinned official PdfCraft CLI for native acceptance only."""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

RUNTIME_DIRECTORY = "pdfcraft-cli-0.13.0-linux-x86_64"
ARCHIVE_NAME = RUNTIME_DIRECTORY + ".tar.gz"
URL = "https://github.com/storytold/pdfcraft/releases/download/v0.13.0/" + ARCHIVE_NAME
ARCHIVE_SHA256 = "fd40f80157856cadd4348a464ef0acde8f19894d831d78e9823708ecf70a3352"
CLI_SHA256 = "2283260fc06a2cadc32c2836fcdd29ed2996418ab3c03291e01a3d20328d157e"
CLI_MEMBER = RUNTIME_DIRECTORY + "/pdfcraft-cli"
MAX_ARCHIVE = 40 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024


def verified_archive(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise ValueError("official runtime archive must be a bounded regular file")
    data = path.read_bytes()
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("official PdfCraft archive SHA-256 mismatch")
    return data


def extract_cli(archive: Path, destination: Path) -> Path:
    """Inspect the verified archive, then publish only its pinned CLI file."""
    data = verified_archive(archive)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 50 or sum(member.size for member in members) > MAX_UNPACKED:
            raise ValueError("unexpected official CLI archive size")
        if len({member.name for member in members}) != len(members):
            raise ValueError("duplicate official runtime archive member")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                "\\" in member.name
                or path.is_absolute()
                or not path.parts
                or path.parts[0] != RUNTIME_DIRECTORY
                or ".." in path.parts
            ):
                raise ValueError("unsafe official runtime archive path")
            if not member.isfile() and not member.isdir():
                raise ValueError("runtime archive links/special files are unsupported")
        member = bundle.getmember(CLI_MEMBER)
        if not member.isfile() or member.size > MAX_UNPACKED:
            raise ValueError("unexpected official CLI archive member")
        stream = bundle.extractfile(member)
        if stream is None:
            raise ValueError("official CLI is missing from archive")
        binary = stream.read(member.size + 1)
    if len(binary) != member.size or hashlib.sha256(binary).hexdigest() != CLI_SHA256:
        raise ValueError("official PdfCraft executable SHA-256 mismatch")
    if destination.is_symlink():
        raise ValueError("refusing a symbolic-link CLI destination")
    if destination.exists():
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != CLI_SHA256:
            raise ValueError("existing CLI destination differs from the pinned executable")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        output.write(binary)
    destination.chmod(0o755)
    return destination


def fetch_runtime(directory: Path) -> Path:
    root = directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / ARCHIVE_NAME
    if not archive.exists() and not archive.is_symlink():
        with urlopen(URL, timeout=120) as response:
            data = response.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
            raise ValueError("official PdfCraft download size/hash mismatch")
        with archive.open("xb") as output:
            output.write(data)
    return extract_cli(archive, root / RUNTIME_DIRECTORY / "pdfcraft-cli")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("runtime"))
    args = parser.parse_args()
    fetch_runtime(args.directory)
    print("Official PdfCraft archive and extracted CLI SHA-256 verified; no runtime was executed.")


if __name__ == "__main__":
    main()
