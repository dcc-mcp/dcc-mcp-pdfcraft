"""Environment variables the adapter honours.

This module exists so that the transport layer can read configuration without
importing the server: the server imports the transports, so a transport that
imported the server back would be circular. Keeping the names here lets both
sides share one definition.
"""

from __future__ import annotations

import os

# Path to the ``pdfcraft-cli`` binary, for installs where it is not on PATH.
ENV_CLI = "PDFCRAFT_CLI"

# Path to the file written by ``pdfcraft --control <file>``. The app writes its
# chosen port and token there on each start.
ENV_CONTROL_FILE = "PDFCRAFT_CONTROL_FILE"

# Optional sandbox root forwarded to ``pdfcraft-cli --root``.
ENV_ROOT = "PDFCRAFT_ROOT"

# Optional absolute portable data root for read-only Craft Launcher hints.
ENV_LAUNCHER_DATA = "PDFCRAFT_LAUNCHER_DATA"


def cli_path() -> str | None:
    """Return the configured CLI path, or ``None`` to fall back to discovery."""
    return os.environ.get(ENV_CLI)


def control_file() -> str | None:
    """Return the configured control-file path, or ``None``."""
    return os.environ.get(ENV_CONTROL_FILE)


def root() -> str | None:
    """Return the configured sandbox root, or ``None``."""
    return os.environ.get(ENV_ROOT)
