"""Headless driving of PdfCraft through its ``pdfcraft-cli`` subprocess.

This is the primary automation path: it needs no window, no display server and
no control channel, and it is the only path that works in CI. PdfCraft
documents ``pdfcraft-cli`` as the surface where "every engine command is one
shell call away, for scripts and AI agents".

The command set is discovered at runtime rather than declared here, because
upstream ships a new minor version every one to two days and grows the tool
table continuously. ``pdfcraft-cli tools`` prints the full table with JSON
Schemas, so a hardcoded list would be stale on the next upstream release.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .host import CLI_BINARY, find_binary

DEFAULT_TIMEOUT_SECS = 120.0


class CliError(RuntimeError):
    """A ``pdfcraft-cli`` invocation failed."""


class CliNotFound(CliError):
    """The ``pdfcraft-cli`` binary could not be located."""


class CliTransport:
    """Run ``pdfcraft-cli`` subcommands and parse their JSON output."""

    def __init__(
        self,
        binary: str | Path | None = None,
        *,
        root: str | Path | None = None,
        timeout_secs: float = DEFAULT_TIMEOUT_SECS,
    ) -> None:
        resolved = Path(binary) if binary is not None else find_binary(CLI_BINARY)
        # An explicitly configured path is checked too: a stale PDFCRAFT_CLI
        # should fail here with a clear message rather than surfacing as an
        # opaque subprocess spawn error on the first tool call.
        if resolved is None or not resolved.is_file():
            raise CliNotFound("pdfcraft-cli was not found; install PdfCraft or set PDFCRAFT_CLI to its path")
        self._binary = str(resolved)
        self._root = str(root) if root is not None else None
        self._timeout_secs = timeout_secs

    @property
    def binary(self) -> str:
        return self._binary

    def _base_args(self) -> list[str]:
        args = [self._binary]
        if self._root:
            # ``--root`` sandboxes reads and writes; upstream resolves symlinks
            # and rejects anything that escapes it.
            args += ["--root", self._root]
        return args

    def run(self, args: list[str], *, timeout_secs: float | None = None) -> str:
        """Run one subcommand and return its stdout, raising on a non-zero exit."""
        command = self._base_args() + list(args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_secs if timeout_secs is None else timeout_secs,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Report the bound that actually applied: a per-call override
            # replaces the instance default, and quoting the default would
            # misreport why the call failed.
            applied = self._timeout_secs if timeout_secs is None else timeout_secs
            raise CliError(f"{' '.join(command)} timed out after {applied}s") from exc
        except OSError as exc:
            raise CliError(f"cannot run {self._binary}: {exc}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise CliError(f"{' '.join(command)} failed with exit {completed.returncode}: {detail}")
        return completed.stdout

    def tools(self) -> list[dict[str, Any]]:
        """Return the tool table with JSON Schemas that upstream publishes.

        This is the source of truth for tool definitions: the adapter maps what
        upstream reports instead of maintaining its own copy.
        """
        output = self.run(["tools"])
        payload = _parse_json(output, "tools")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            tools = payload.get("tools")
            if isinstance(tools, list):
                return [item for item in tools if isinstance(item, dict)]
        raise CliError("pdfcraft-cli tools returned an unexpected payload shape")

    def commands(self) -> list[dict[str, Any]]:
        """Return the engine command table."""
        output = self.run(["commands", "--json"])
        payload = _parse_json(output, "commands")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            commands = payload.get("commands")
            if isinstance(commands, list):
                return [item for item in commands if isinstance(item, dict)]
        return []

    def call_tool(self, tool: str, params: dict[str, Any] | None = None) -> Any:
        """Run one named tool through ``pdfcraft-cli run``."""
        args = ["run", tool]
        for key, value in (params or {}).items():
            args.append(f"{key}={_encode_value(value)}")
        return _parse_json(self.run(args), f"run {tool}")

    def info(self, pdf: str | Path) -> dict[str, Any]:
        """Return the JSON summary of one PDF document."""
        payload = _parse_json(self.run(["info", str(pdf)]), "info")
        return payload if isinstance(payload, dict) else {"result": payload}


def _encode_value(value: Any) -> str:
    """Render one argument value the way ``pdfcraft-cli run`` expects."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json(output: str, what: str) -> Any:
    text = output.strip()
    if not text:
        raise CliError(f"pdfcraft-cli {what} produced no output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Upstream writes progress or warnings around the JSON payload in some
        # subcommands, so fall back to the last line that parses as JSON.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise CliError(f"pdfcraft-cli {what} produced no JSON")
