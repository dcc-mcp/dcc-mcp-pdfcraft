"""Tests for the doctor self-check.

The doctor is what an agent reads before it calls anything else, so the
distinctions it draws matter more than its formatting. The one that matters
most here: a machine with only the CLI installed is *usable*, not broken,
because every headless capability works without the desktop app.
"""

from __future__ import annotations

import json

import pytest

from dcc_mcp_pdfcraft import doctor


@pytest.fixture
def clean_env(monkeypatch):
    for name in doctor.ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _no_binaries(monkeypatch, tmp_path):
    from dcc_mcp_pdfcraft import host

    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])


def test_missing_host_is_reportable_not_error(monkeypatch, tmp_path, clean_env):
    _no_binaries(monkeypatch, tmp_path)

    report = doctor.doctor_report()

    # A missing host is a state, not a failure: only a malformed configuration
    # earns the "error" status.
    assert report["status"] == "missing_host"
    assert report["headless_ready"] is False
    assert report["ui_ready"] is False


def test_cli_only_is_usable(monkeypatch, tmp_path, clean_env):
    """The CLI alone covers every headless action, so this is not broken."""
    from dcc_mcp_pdfcraft import host

    fake = tmp_path / ("pdfcraft-cli" + host._SUFFIXES[0])
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])
    monkeypatch.setattr(host, "cli_version", lambda *a, **k: None)

    report = doctor.doctor_report()

    assert report["status"] == "ok"
    assert report["headless_ready"] is True
    assert report["ui_ready"] is False
    assert report["checks"]["desktop_app"]["status"] == "missing"


def test_unset_control_file_is_not_an_error(monkeypatch, tmp_path, clean_env):
    _no_binaries(monkeypatch, tmp_path)

    check = doctor._check_control_file()

    assert check["status"] == "not_configured"
    assert "PDFCRAFT_CONTROL_FILE" in check["detail"]


def test_malformed_control_file_is_an_error(monkeypatch, tmp_path, clean_env):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("PDFCRAFT_CONTROL_FILE", str(path))

    check = doctor._check_control_file()

    assert check["status"] == "error"
    assert check["remediation"]


def test_valid_control_file_never_leaks_the_token(monkeypatch, tmp_path, clean_env):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"port": 8123, "token": "super-secret-token"}), encoding="utf-8")
    monkeypatch.setenv("PDFCRAFT_CONTROL_FILE", str(path))

    check = doctor._check_control_file()

    assert check["status"] == "ok"
    assert "8123" in check["detail"]
    # The token authenticates a local control channel; printing it would turn a
    # diagnostic into a disclosure.
    assert "super-secret-token" not in check["detail"]
    assert "super-secret-token" not in json.dumps(check)


def test_report_is_json_serializable(monkeypatch, tmp_path, clean_env):
    _no_binaries(monkeypatch, tmp_path)

    # An agent consumes this as JSON, so a non-serialisable value would fail at
    # the worst moment.
    assert json.dumps(doctor.doctor_report())


def test_main_returns_zero_for_missing_host(monkeypatch, tmp_path, clean_env, capsys):
    _no_binaries(monkeypatch, tmp_path)

    assert doctor.main([]) == 0
    assert "missing_host" in capsys.readouterr().out


def test_main_returns_one_for_configuration_error(monkeypatch, tmp_path, clean_env):
    _no_binaries(monkeypatch, tmp_path)
    path = tmp_path / "bad.json"
    path.write_text("nonsense", encoding="utf-8")
    monkeypatch.setenv("PDFCRAFT_CONTROL_FILE", str(path))

    assert doctor.main(["--json"]) == 1


def test_main_json_output_is_parseable(monkeypatch, tmp_path, clean_env, capsys):
    _no_binaries(monkeypatch, tmp_path)

    doctor.main(["--json"])

    assert json.loads(capsys.readouterr().out)["dcc"] == "pdfcraft"
