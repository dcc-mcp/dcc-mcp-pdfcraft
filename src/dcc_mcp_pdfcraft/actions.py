"""Adapter-owned MCP actions that are not part of the PdfCraft tool table.

The shipped skill catalog carries the PdfCraft-owned actions. The actions
registered here are the adapter's own: they report what the host exposes and
how healthy it is, and they are the ones an agent calls first to decide what
else it can do.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from .capability_dispatch import ActionError, active_server, dispatch
from .host import probe


def _envelope(result: Any) -> dict[str, Any]:
    return {"ok": True, "result": result}


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


def host_status() -> dict[str, Any]:
    """Report the detected PdfCraft binaries and version.

    Safe to call when PdfCraft is absent: a missing host is a reportable state,
    not a failure, so an agent can distinguish "not installed" from "broken".
    """
    instance = active_server.get()
    status = probe(cli=instance.cli_binary if instance is not None else None)
    return _envelope(
        {"available": status.cli_available, "detail": status.describe(), "cli_location_hint": status.location_hint()}
    )


def list_pdfcraft_tools() -> dict[str, Any]:
    """List the tool table that the installed PdfCraft publishes.

    The list is read from the host at call time. Upstream ships a new minor
    version every one to two days and grows this table continuously, so a
    cached or hardcoded copy would be stale on the next release.
    """
    try:
        return _envelope(dispatch("list_tools"))
    except ActionError as exc:
        return _error(exc)


def list_pdfcraft_commands() -> dict[str, Any]:
    """List the engine command table that the installed PdfCraft publishes."""
    try:
        return _envelope(dispatch("list_commands"))
    except ActionError as exc:
        return _error(exc)


def pdf_info(path: str) -> dict[str, Any]:
    """Return the JSON summary of one PDF document."""
    try:
        return _envelope(dispatch("pdf_info", {"path": path}))
    except ActionError as exc:
        return _error(exc)


ADAPTER_ACTIONS = {
    "host_status": host_status,
    "list_pdfcraft_tools": list_pdfcraft_tools,
    "list_pdfcraft_commands": list_pdfcraft_commands,
    "pdf_info": pdf_info,
}


def register_actions(server: Any) -> None:
    """Attach backward-compatible Python helpers with instance configuration.

    Core registration is performed by the shipped skill catalog. These helpers
    are conveniences for Python callers, not substitutes for skill discovery.
    """

    def bind(func):
        @wraps(func)
        def call(*args, **kwargs):
            token = active_server.set(server)
            try:
                return func(*args, **kwargs)
            finally:
                active_server.reset(token)

        return call

    for name, func in ADAPTER_ACTIONS.items():
        setattr(server, name, bind(func))
