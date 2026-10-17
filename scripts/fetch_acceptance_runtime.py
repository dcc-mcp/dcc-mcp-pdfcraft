"""Opt-in, hash-pinned official runtimes for the cross-app acceptance job only."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

RUNTIMES = {
    "filmcraft-web-0.7.2.zip": (
        "https://github.com/storytold/filmcraft/releases/download/v0.7.2/filmcraft-web-0.7.2.zip",
        "48d4f48728fa6b5c028612a4b7f59a5832ff38d430c6a7bd417a2c77d75ae4f9",
        20 * 1024 * 1024,
    ),
    "pdfcraft-cli-0.7.1-linux-x86_64.tar.gz": (
        "https://github.com/storytold/pdfcraft/releases/download/v0.7.1/pdfcraft-cli-0.7.1-linux-x86_64.tar.gz",
        "1916000d20b1a5252f631e148ffcfed8e5c178e9b5c0cf67da864d2cc33ca00b",
        40 * 1024 * 1024,
    ),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("runtime"))
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name, (url, sha, limit) in RUNTIMES.items():
        path = root / name
        if not path.exists():
            with urlopen(url, timeout=120) as response:
                data = response.read(limit + 1)
            if len(data) > limit or hashlib.sha256(data).hexdigest() != sha:
                raise ValueError("official runtime size/hash mismatch: " + name)
            path.write_bytes(data)
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError("existing runtime hash mismatch: " + name)
    archive = root / "pdfcraft-cli-0.7.1-linux-x86_64.tar.gz"
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if len(members) > 50 or sum(member.size for member in members) > 100 * 1024 * 1024:
            raise ValueError("unexpected official CLI archive size")
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != "pdfcraft-cli-0.7.1-linux-x86_64" or ".." in parts:
                raise ValueError("unsafe runtime archive path")
            if not member.isfile() and not member.isdir():
                raise ValueError("runtime archive links/special files are unsupported")
            target = root.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ValueError("unreadable runtime member")
                target.write_bytes(stream.read(member.size + 1))
                if target.name == "pdfcraft-cli":
                    target.chmod(0o755)
    print("Official FilmCraft browser ZIP and native PdfCraft CLI verified.")


if __name__ == "__main__":
    main()
