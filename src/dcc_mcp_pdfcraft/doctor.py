"""Self-check for the PdfCraft adapter.

The doctor answers one question an agent cannot answer any other way: is this
machine actually able to drive PdfCraft right now, and if not, which of the two
host surfaces is missing? Reporting the two separately matters because they
enable different things -- the CLI alone supports every headless capability,
while the desktop app adds the UI-driven ones -- so a machine with only the CLI
installed is usable, not broken.

Every check is read-only. Nothing here starts the app or opens a port.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .control_channel import ControlChannelUnavailable, read_endpoint
from .host import APP_BINARY, CLI_BINARY, probe

# Environment variables the adapter honours, reported so a stale setting is
# visible next to the binaries it overrides.
ENV_VARS = ("PDFCRAFT_CLI", "PDFCRAFT_CONTROL_FILE", "PDFCRAFT_ROOT")


def _check_cli(status: Any) -> dict[str, Any]:
    if status.cli_available:
        return {
            "status": "ok",
            "detail": f"found {status.binaries.cli}" + (f" (version {status.version})" if status.version else ""),
        }
    return {
        "status": "missing",
        "detail": f"{CLI_BINARY} not found; install PdfCraft or set PDFCRAFT_CLI",
        "remediation": "Install PdfCraft, or point PDFCRAFT_CLI at the pdfcraft-cli binary.",
    }


def _check_app(status: Any) -> dict[str, Any]:
    if status.app_available:
        return {"status": "ok", "detail": f"found {status.binaries.app}"}
    return {
        "status": "missing",
        "detail": f"{APP_BINARY} not found; UI-driven actions are unavailable",
        # Deliberately not an error: the CLI alone covers headless work.
        "remediation": "Optional. Install the PdfCraft desktop app to enable UI-driven actions.",
    }


def _check_control_file() -> dict[str, Any]:
    path = os.environ.get("PDFCRAFT_CONTROL_FILE")
    if not path:
        return {
            "status": "not_configured",
            "detail": "PDFCRAFT_CONTROL_FILE is unset; UI-driven actions are unavailable",
            "remediation": "Start PdfCraft with --control <file> and set this to that path.",
        }
    try:
        endpoint = read_endpoint(path)
    except ControlChannelUnavailable as exc:
        return {
            "status": "error",
            "detail": str(exc),
            "remediation": "Restart PdfCraft with --control <file>; the file is rewritten each start.",
        }
    # The token is a secret: report that one was present, never its value.
    return {"status": "ok", "detail": f"control file {path} has port {endpoint.port} and a token"}


def doctor_report() -> dict[str, Any]:
    """Return the full self-check as a JSON-serialisable dict."""
    status = probe()
    checks = {
        "cli": _check_cli(status),
        "desktop_app": _check_app(status),
        "control_file": _check_control_file(),
        "environment": {name: os.environ.get(name) for name in ENV_VARS},
    }
    errors = [name for name, check in checks.items() if isinstance(check, dict) and check.get("status") == "error"]
    return {
        "adapter": "dcc-mcp-pdfcraft",
        "dcc": "pdfcraft",
        "status": "error" if errors else ("ok" if status.cli_available else "missing_host"),
        "headless_ready": status.cli_available,
        "ui_ready": status.app_available and checks["control_file"]["status"] == "ok",
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the doctor from the command line.

    Falls back to ``sys.argv`` so ``python -m dcc_mcp_pdfcraft.doctor --json``
    honours its flags: the module entry point is the only real one, and
    defaulting to an empty list would make ``--json`` a dead switch.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    report = doctor_report()
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"{report['adapter']}: {report['status']}")
        for name, check in report["checks"].items():
            if name == "environment":
                continue
            print(f"  {name}: {check['status']} - {check['detail']}")
    # A missing host is a reportable state, not a failure: only a malformed
    # configuration is an error worth a non-zero exit.
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
