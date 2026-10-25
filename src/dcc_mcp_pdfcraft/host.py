"""Locating and probing the PdfCraft binaries on the local machine.

PdfCraft has no embedded Python interpreter, so the adapter always runs in its
own process and drives the host from the outside. Two host entry points matter:

* ``pdfcraft-cli`` -- headless; every engine command is one shell call away.
* ``pdfcraft`` -- the desktop app; starts a control channel with ``--control``.

Neither is installed into a fixed location. Upstream ships the CLI inside the
GUI packages (the Windows portable zip holds ``pdfcraft-cli.exe`` and the
Linux packages install it to ``/usr/bin``), and publishes it as a standalone
archive for macOS and Linux only. Detection therefore searches ``PATH`` first
and then per-platform install directories, which is what keeps this working
whether the user installed a package or unpacked an archive. Operator CLI
configuration takes precedence; Launcher paths are read-only location hints.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import launcher
from .env import ENV_CLI

CLI_BINARY = "pdfcraft-cli"
APP_BINARY = "pdfcraft"

if sys.platform == "win32":
    _SUFFIXES = (".exe", ".cmd", ".bat")
else:
    _SUFFIXES = ("",)


@dataclass(frozen=True)
class HostBinaries:
    """The resolved PdfCraft executables, when present."""

    cli: Path | None
    app: Path | None
    cli_source: str | None = None
    configuration_error: str | None = None


def _candidate_names(binary: str) -> list[str]:
    return [binary + suffix for suffix in _SUFFIXES]


def _which(binary: str) -> Path | None:
    for name in _candidate_names(binary):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _search_dirs() -> list[Path]:
    """Per-platform directories where PdfCraft packages install the CLI."""
    home = Path.home()
    if sys.platform == "win32":
        roots = [
            Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")),
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")),
        ]
        return [root / "PdfCraft" for root in roots] + [root / "PdfCraft" / "bin" for root in roots]

    if sys.platform == "darwin":
        return [
            Path("/Applications/PdfCraft.app/Contents/MacOS"),
            home / "Applications/PdfCraft.app/Contents/MacOS",
            Path("/usr/local/bin"),
        ]

    return [Path("/usr/bin"), Path("/usr/local/bin"), home / ".local" / "bin"]


def _find_in_search_dirs(binary: str) -> Path | None:
    return _find_in_directories(binary, _search_dirs())


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except (OSError, ValueError):
        return False


def _find_in_directories(binary: str, directories: list[Path]) -> Path | None:
    for directory in directories:
        for name in _candidate_names(binary):
            candidate = directory / name
            if _usable(candidate):
                return candidate
    return None


def _resolve_cli(explicit: Path | None = None) -> tuple[Path | None, str | None, str | None]:
    configured = explicit if explicit is not None else os.environ.get(ENV_CLI)
    if configured is not None:
        source = "argument" if explicit is not None else "environment"
        try:
            candidate = Path(configured).expanduser()
            if _usable(candidate):
                return candidate.resolve(), source, None
        except (OSError, ValueError):
            pass
        return None, source, "configured PdfCraft CLI is not an executable file"
    located = _which(CLI_BINARY)
    if located is not None and _usable(located):
        return located, "path", None
    located = _find_in_directories(CLI_BINARY, launcher.search_directories())
    if located is not None:
        return located, "craft-launcher", None
    located = _find_in_search_dirs(CLI_BINARY)
    return located, "installation-directory" if located is not None else None, None


def find_binary(binary: str) -> Path | None:
    """Resolve one PdfCraft executable, or return ``None`` when it is absent."""
    if binary == CLI_BINARY:
        return resolve_cli_path()
    return _which(binary) or _find_in_directories(binary, launcher.search_directories()) or _find_in_search_dirs(binary)


def resolve_cli_path(explicit: Path | None = None) -> Path | None:
    """Select the CLI with the same policy for diagnostics and real calls."""
    return _resolve_cli(explicit)[0]


def discover(cli: Path | None = None) -> HostBinaries:
    """Locate both PdfCraft executables without invoking either of them.

    Detection is a filesystem lookup only: nothing here spawns a subprocess,
    so it stays safe to call from skill discovery and from ``--help``.
    """
    resolved, source, error = _resolve_cli(cli)
    return HostBinaries(cli=resolved, app=find_binary(APP_BINARY), cli_source=source, configuration_error=error)


def cli_version(cli: Path | None = None) -> str | None:
    """Return the version reported by ``pdfcraft-cli --version``.

    ``None`` means the version could not be determined, which is not an error:
    the binary may be an older build without the flag.
    """
    resolved = resolve_cli_path(cli)
    if resolved is None:
        return None
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


@dataclass(frozen=True)
class HostStatus:
    """What the adapter can do with the host right now."""

    binaries: HostBinaries
    version: str | None

    @property
    def cli_available(self) -> bool:
        return self.binaries.cli is not None

    @property
    def app_available(self) -> bool:
        return self.binaries.app is not None

    @property
    def configuration_error(self) -> str | None:
        return self.binaries.configuration_error

    def location_hint(self) -> dict:
        return {
            "source": self.binaries.cli_source,
            "binary_trust_verified": False,
            "gui_connection_verified": False,
            "automatic_install": False,
        }

    def describe(self) -> str:
        if self.configuration_error:
            return self.configuration_error + "; fix PDFCRAFT_CLI or the explicit CLI path"
        if not self.cli_available and not self.app_available:
            return "PdfCraft not found; install it or put pdfcraft-cli on PATH"
        parts = []
        if self.cli_available:
            parts.append(f"cli={self.binaries.cli}")
        if self.app_available:
            parts.append(f"app={self.binaries.app}")
        if self.version:
            parts.append(f"version={self.version}")
        return ", ".join(parts)


def probe(cli: Path | None = None) -> HostStatus:
    """Report the host binaries and version without starting the app."""
    binaries = discover(cli)
    return HostStatus(binaries=binaries, version=cli_version(binaries.cli) if binaries.cli is not None else None)
