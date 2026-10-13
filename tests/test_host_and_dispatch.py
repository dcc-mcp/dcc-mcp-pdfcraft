"""Tests for host detection and action dispatch.

A recurring failure mode for an adapter that drives an external binary is
reporting success when the host is missing. These tests pin the opposite
contract: a missing host is reported, never faked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcc_mcp_pdfcraft import actions, capability_dispatch, host
from dcc_mcp_pdfcraft.capability_dispatch import ActionError, dispatch


def test_discover_reports_absent_binaries(monkeypatch, tmp_path):
    """With nothing on PATH, both binaries must be reported as absent."""
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    status = host.probe()

    assert status.cli_available is False
    assert status.app_available is False
    assert "not found" in status.describe()


def test_discover_finds_cli_in_search_dir(monkeypatch, tmp_path):
    # The candidate name is platform-specific (.exe on Windows), so take it from
    # the module under test rather than assuming a bare name.
    fake = tmp_path / ("pdfcraft-cli" + host._SUFFIXES[0])
    fake.write_text("", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | 0o111)
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    assert host.discover().cli is not None
    assert host.probe().cli_available is True


def test_discover_does_not_execute_the_binary(monkeypatch, tmp_path):
    """Detection must be a filesystem lookup, not a subprocess spawn."""
    calls = []
    monkeypatch.setattr(host, "cli_version", lambda *a, **k: calls.append(True))
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    host.discover()

    assert calls == []


def test_actions_report_missing_host_without_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    report = actions.host_status()

    # A missing host is reportable state, not an exception.
    assert report["ok"] is True
    assert report["result"]["available"] is False


def test_list_tools_reports_error_when_host_missing(monkeypatch, tmp_path, monkeypatch_env):
    monkeypatch.setattr(capability_dispatch, "_transport", _missing_cli)

    report = actions.list_pdfcraft_tools()

    assert report["ok"] is False
    assert "error" in report


def _missing_cli(*args, **kwargs):
    from dcc_mcp_pdfcraft.cli_transport import CliNotFound

    raise CliNotFound("pdfcraft-cli was not found")


@pytest.fixture
def monkeypatch_env(monkeypatch):
    """Clear the adapter environment so tests do not inherit a real host."""
    for name in ("PDFCRAFT_CLI", "PDFCRAFT_CONTROL_FILE", "PDFCRAFT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_dispatch_routes_cli_actions(tmp_path, monkeypatch, monkeypatch_env):
    seen = {}

    class FakeTransport:
        def __init__(self, *args, **kwargs):
            seen["kwargs"] = kwargs

        def tools(self):
            return [{"name": "page_rotate"}]

        def commands(self):
            return [{"id": "page.rotate"}]

        def info(self, path):
            seen["path"] = path
            return {"pages": 2}

        def call_tool(self, name, params):
            seen["tool"] = name
            seen["params"] = params
            return {"ok": True}

    monkeypatch.setattr(capability_dispatch, "_transport", lambda: FakeTransport())

    assert dispatch("list_tools")["tools"] == [{"name": "page_rotate"}]
    assert dispatch("pdf_info", {"path": "a.pdf"})["result"] == {"pages": 2}
    assert seen["path"] == "a.pdf"

    assert dispatch("list_pdfcraft_tools")["tools"] == [{"name": "page_rotate"}]
    assert dispatch("list_pdfcraft_commands")["commands"] == [{"id": "page.rotate"}]
    with pytest.raises(ActionError, match="Unsupported adapter action"):
        dispatch("page_rotate", {"degrees": 90})


def test_dispatch_requires_path_for_pdf_info(monkeypatch):
    monkeypatch.setattr(capability_dispatch, "_transport", lambda: object())

    with pytest.raises(ActionError, match="requires a `path`"):
        dispatch("pdf_info", {})


def test_ui_action_without_control_file_explains_how_to_fix(monkeypatch_env):
    with pytest.raises(ActionError) as excinfo:
        dispatch("ui_screenshot")

    message = str(excinfo.value)
    assert "--control" in message
    assert "PDFCRAFT_CONTROL_FILE" in message


def test_ui_action_uses_control_channel(monkeypatch, tmp_path, control_file, fake_server, monkeypatch_env):
    """A UI action must reach the control channel and authenticate."""
    monkeypatch.setenv("PDFCRAFT_CONTROL_FILE", str(control_file))

    class FakeServer:
        def control_channel(self):
            from dcc_mcp_pdfcraft.control_channel import ControlChannel, read_endpoint

            return ControlChannel(read_endpoint(control_file))

    monkeypatch.setattr(capability_dispatch, "_server", lambda: FakeServer())

    result = dispatch("ui_screenshot", {"page": 1})

    assert result["method"] == "ui.screenshot"
    assert fake_server.received[-1]["params"] == {"page": 1}


def test_action_error_is_normalized_in_envelope(monkeypatch):
    monkeypatch.setattr(capability_dispatch, "_transport", _missing_cli)

    report = actions.pdf_info("a.pdf")

    assert report["ok"] is False
    assert report["error_type"] == "ActionError"


def test_read_endpoint_accepts_string_path(tmp_path):
    """Callers pass paths from environment variables, so strings must work."""
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"port": 9000, "token": "t"}), encoding="utf-8")

    from dcc_mcp_pdfcraft.control_channel import read_endpoint

    endpoint = read_endpoint(str(path))
    assert endpoint.port == 9000
    assert isinstance(Path(str(path)), Path)
