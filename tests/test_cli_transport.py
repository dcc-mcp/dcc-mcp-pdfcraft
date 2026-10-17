"""Tests for the headless ``pdfcraft-cli`` transport.

These use a stub binary instead of a real PdfCraft install, so they run in CI
without Rust and without the app. What they pin down is the contract on this
side of the process boundary: argument construction, exit-code handling, and
JSON parsing.
"""

from __future__ import annotations

import json
import shlex
import stat
import sys
from pathlib import Path

import pytest

from dcc_mcp_pdfcraft.cli_transport import CliError, CliNotFound, CliTransport


def _write_stub(tmp_path: Path, lines: list[str]) -> Path:
    """Write an executable stub that prints ``lines`` to stdout, one per line.

    Lines are taken literally rather than as shell syntax so the same fixture
    works on Windows, where the stub is a batch file, and on POSIX, where it is
    a shell script.
    """
    if sys.platform == "win32":
        path = tmp_path / "pdfcraft-cli.bat"
        body = "@echo off\r\n" + "".join(f"echo {line}\r\n" for line in lines)
        path.write_text(body, encoding="utf-8")
    else:
        path = tmp_path / "pdfcraft-cli"
        body = "#!/bin/sh\n" + "".join(f"printf '%s\\n' {shlex.quote(line)}\n" for line in lines)
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _echo_stub(tmp_path: Path, payload: str) -> Path:
    """A stub that prints one fixed line, ignoring its arguments."""
    return _write_stub(tmp_path, [payload])


def test_missing_binary_raises_not_found(tmp_path):
    with pytest.raises(CliNotFound):
        CliTransport(tmp_path / "does-not-exist")


def test_tools_parses_json_array(tmp_path):
    stub = _echo_stub(tmp_path, '[{"name":"page_rotate","input_schema":{"type":"object"}}]')

    tools = CliTransport(stub).tools()

    assert tools == [{"name": "page_rotate", "input_schema": {"type": "object"}}]


def test_tools_parses_object_with_tools_key(tmp_path):
    stub = _echo_stub(tmp_path, '{"tools":[{"name":"doc_open"}]}')

    assert CliTransport(stub).tools() == [{"name": "doc_open"}]


def test_tools_rejects_unexpected_shape(tmp_path):
    stub = _echo_stub(tmp_path, '{"unexpected":true}')

    with pytest.raises(CliError, match="unexpected payload shape"):
        CliTransport(stub).tools()


def test_non_zero_exit_is_reported(tmp_path):
    if sys.platform == "win32":
        path = tmp_path / "pdfcraft-cli.bat"
        path.write_text("@echo off\r\necho boom 1>&2\r\nexit /b 3\r\n", encoding="utf-8")
    else:
        path = tmp_path / "pdfcraft-cli"
        path.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(CliError, match="exit 3"):
        CliTransport(path).run(["info", "x.pdf"])


def test_empty_output_raises(tmp_path):
    stub = _echo_stub(tmp_path, "")

    with pytest.raises(CliError, match="no JSON|no output"):
        CliTransport(stub).tools()


def test_root_is_forwarded(tmp_path, monkeypatch):
    """``--root`` must reach the binary, since it is the sandbox boundary."""
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(list(command))
        import subprocess

        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "pdfcraft-cli"
    stub.write_text("", encoding="utf-8")

    CliTransport(stub, root="/work").run(["run", "--script", "steps.json"])

    assert captured[0][1:4] == ["run", "--script", "steps.json"]
    assert "--root" in captured[0]
    assert captured[0][captured[0].index("--root") + 1] == "/work"


def test_call_tool_encodes_json_values(tmp_path, monkeypatch):
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(list(command))
        import subprocess

        return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "pdfcraft-cli"
    stub.write_text("", encoding="utf-8")

    result = CliTransport(stub).call_tool("page_rotate", {"pages": [1, 3], "degrees": 90})

    assert result == {"ok": True}
    args = captured[0]
    assert "page_rotate" in args
    # Non-string values are JSON-encoded so nested structures survive the
    # command line, as one ``key=value`` argument each.
    assert "pages=" + json.dumps([1, 3]) in args
    assert "degrees=90" in args


def test_json_is_recovered_from_surrounding_output(tmp_path):
    """Upstream writes progress lines around the payload in some subcommands."""
    stub = _write_stub(tmp_path, ["opening document...", '{"pages": 3}'])

    assert CliTransport(stub).info("x.pdf") == {"pages": 3}


def test_discovery_does_not_pass_unsupported_root_flag(tmp_path, monkeypatch):
    calls = []
    stub = tmp_path / "pdfcraft-cli"
    stub.touch()

    def fake_run(command, **kwargs):
        import subprocess

        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='[{"name":"doc_open","command":"file.open"}]')

    monkeypatch.setattr("subprocess.run", fake_run)
    transport = CliTransport(stub, root=tmp_path)
    assert transport.commands() == [{"id": "file.open", "tools": ["doc_open"]}]
    assert calls == [[str(stub), "tools"]]


def test_info_cannot_escape_configured_root(tmp_path):
    stub = _echo_stub(tmp_path, "{}")
    with pytest.raises(CliError, match="escapes"):
        CliTransport(stub, root=tmp_path).info(tmp_path.parent / "outside.pdf")
