"""Bounded, read-only native location hints from the official Craft Launcher.

The Launcher retains installation and update ownership. A manifest path is not
binary provenance, a running desktop identity, or permission to attach to it.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from .env import ENV_LAUNCHER_DATA

MAX_MANIFEST_BYTES = 1024 * 1024


def data_directories() -> list[Path]:
    """Known Launcher roots plus an optional absolute portable-data hint."""
    home = Path.home()
    configured = os.environ.get(ENV_LAUNCHER_DATA)
    roots = [Path(configured)] if configured and Path(configured).is_absolute() else []
    if sys.platform == "win32":
        roots.append(Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "ArtCraft Launcher")
    elif sys.platform == "darwin":
        roots.append(home / "Library/Application Support/ArtCraft Launcher")
    else:
        roots.extend(
            [
                Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "artcraft-launcher",
                home / ".var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher",
            ]
        )
    return list(dict.fromkeys(roots))


def _record_path(directory: Path) -> Path | None:
    try:
        manifest = directory / "installed.json"
        info = manifest.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFEST_BYTES:
            return None
        with manifest.open("rb") as stream:
            data = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            return None
        value = json.loads(data)["apps"]["pdfcraft"]["path"]
        if not isinstance(value, str) or not value or "\x00" in value:
            return None
        path = Path(value)
        return path if path.is_absolute() else None
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def search_directories() -> list[Path]:
    """Portable siblings, native trees and macOS bundles; never unpack files."""
    found = []
    for directory in data_directories():
        recorded = _record_path(directory)
        if recorded is not None:
            if recorded.name.lower() in {"pdfcraft", "pdfcraft.exe"}:
                found.append(recorded.parent)
            elif recorded.name == "PdfCraft.app":
                found.append(recorded / "Contents/MacOS")
            else:
                try:
                    if recorded.is_dir():
                        found.extend([recorded / "bin", recorded])
                except (OSError, ValueError):
                    pass
        tree = directory / "apps/pdfcraft"
        found.extend([tree / "bin", tree])
    return list(dict.fromkeys(found))
