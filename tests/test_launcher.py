"""Launcher hints and configured paths never grant execution or GUI authority."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_mcp_pdfcraft import actions, capability_dispatch, cli, doctor, host, launcher
from dcc_mcp_pdfcraft.capability_dispatch import active_server
from dcc_mcp_pdfcraft.cli_transport import CliNotFound, CliTransport


@pytest.fixture(autouse=True)
def isolated_discovery(monkeypatch, tmp_path):
    for name in ("PDFCRAFT_CLI", "PDFCRAFT_LAUNCHER_DATA", "PDFCRAFT_CONTROL_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host, "_which", lambda _: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [])
    monkeypatch.setattr(launcher, "data_directories", lambda: [])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("test launched an unverified executable"))


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test-only placeholder; never execute")
    path.chmod(0o700)
    return path


def cli_name():
    return host._candidate_names(host.CLI_BINARY)[0]


@pytest.mark.parametrize("layout", ["portable", "tree", "bundle"])
def test_launcher_finds_native_cli_without_execution_or_manifest_changes(monkeypatch, tmp_path, layout):
    data = tmp_path / "data"
    data.mkdir()
    install = tmp_path / "install"
    if layout == "portable":
        recorded = executable(install / "pdfcraft.exe")
        directory = install
    elif layout == "bundle":
        recorded = install / "PdfCraft.app"
        directory = recorded / "Contents/MacOS"
    else:
        recorded = install
        directory = install / "bin"
    expected = executable(directory / cli_name())
    manifest = data / "installed.json"
    manifest.write_text(json.dumps({"apps": {"pdfcraft": {"path": str(recorded)}}}), encoding="utf-8")
    before = manifest.read_bytes()
    monkeypatch.setattr(launcher, "data_directories", lambda: [data])

    found = host.discover()

    assert found.cli == expected
    assert found.cli_source == "craft-launcher"
    assert manifest.read_bytes() == before
    assert host.HostStatus(found, None).location_hint() == {
        "source": "craft-launcher",
        "binary_trust_verified": False,
        "gui_connection_verified": False,
        "automatic_install": False,
    }


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        "[]",
        '{"apps":[]}',
        '{"apps":{"pdfcraft":{"path":"relative/bin"}}}',
        '{"apps":{"pdfcraft":{"path":42}}}',
        '{"apps":{"pdfcraft":{"path":""}}}',
        '{"apps":{"photocraft":{"path":"/another-product"}}}',
        '{"apps":{"pdfcraft":{"path":"/bad\\u0000path"}}}',
    ],
)
def test_malformed_manifest_is_only_an_absent_hint(monkeypatch, tmp_path, payload):
    (tmp_path / "installed.json").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(launcher, "data_directories", lambda: [tmp_path])
    assert launcher._record_path(tmp_path) is None
    assert host.discover().cli is None


def test_manifest_size_and_regular_file_gates(tmp_path):
    manifest = tmp_path / "installed.json"
    manifest.write_bytes(b" " * (launcher.MAX_MANIFEST_BYTES + 1))
    assert launcher._record_path(tmp_path) is None
    manifest.unlink()
    manifest.mkdir()
    assert launcher._record_path(tmp_path) is None


@pytest.mark.skipif(os.name == "nt", reason="symlink privileges vary on Windows")
def test_manifest_symlink_is_not_read(tmp_path):
    source = tmp_path / "other.json"
    source.write_text('{"apps":{"pdfcraft":{"path":"/unused"}}}')
    (tmp_path / "installed.json").symlink_to(source)
    assert launcher._record_path(tmp_path) is None


def test_default_native_tree_and_appimage_without_unpacking(monkeypatch, tmp_path):
    image = tmp_path / "PdfCraft.AppImage"
    image.write_bytes(b"never unpacked")
    data = tmp_path / "data"
    data.mkdir()
    (data / "installed.json").write_text(json.dumps({"apps": {"pdfcraft": {"path": str(image)}}}))
    native = executable(data / "apps/pdfcraft/bin" / cli_name())
    monkeypatch.setattr(launcher, "data_directories", lambda: [data])
    assert host.discover().cli == native
    assert image.read_bytes() == b"never unpacked"


def test_unreadable_recorded_tree_is_ignored(monkeypatch, tmp_path):
    recorded = tmp_path / "unreadable"
    (tmp_path / "installed.json").write_text(json.dumps({"apps": {"pdfcraft": {"path": str(recorded)}}}))
    original = Path.is_dir

    def is_dir(path):
        if path == recorded:
            raise PermissionError("unreadable installation")
        return original(path)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    monkeypatch.setattr(launcher, "data_directories", lambda: [tmp_path])
    assert host.discover().cli is None


def test_configured_cli_then_path_then_launcher_then_platform_directory(monkeypatch, tmp_path):
    configured = executable(tmp_path / "configured")
    in_path = executable(tmp_path / "on-path")
    in_launcher = executable(tmp_path / "launcher" / cli_name())
    in_platform = executable(tmp_path / "platform" / cli_name())
    monkeypatch.setenv("PDFCRAFT_CLI", str(configured))
    monkeypatch.setattr(host, "_which", lambda binary: in_path if binary == host.CLI_BINARY else None)
    monkeypatch.setattr(launcher, "search_directories", lambda: [in_launcher.parent])
    monkeypatch.setattr(host, "_search_dirs", lambda: [in_platform.parent])

    assert host.discover().cli == configured.resolve()
    assert host.discover().cli_source == "environment"
    monkeypatch.delenv("PDFCRAFT_CLI")
    assert host.discover().cli == in_path
    assert host.discover().cli_source == "path"
    monkeypatch.setattr(host, "_which", lambda _: None)
    assert host.discover().cli == in_launcher
    monkeypatch.setattr(launcher, "search_directories", lambda: [])
    assert host.discover().cli == in_platform
    assert host.discover().cli_source == "installation-directory"


@pytest.mark.parametrize("configured", ["missing", "directory", "empty"])
def test_invalid_explicit_configuration_never_falls_back_or_spawns(monkeypatch, tmp_path, configured):
    path = tmp_path / configured
    if configured == "directory":
        path.mkdir()
    value = "" if configured == "empty" else str(path)
    monkeypatch.setenv("PDFCRAFT_CLI", value)
    fallback = executable(tmp_path / "fallback")
    monkeypatch.setattr(host, "_which", lambda binary: fallback if binary == host.CLI_BINARY else None)
    status = host.probe()
    assert not status.cli_available
    assert status.configuration_error
    assert status.version is None
    assert host.find_binary(host.CLI_BINARY) is None
    assert doctor.doctor_report()["status"] == "error"
    with pytest.raises(CliNotFound):
        CliTransport()
    with pytest.raises(CliNotFound):
        capability_dispatch._transport()


def test_explicit_probe_path_replaces_environment_in_status_and_version_probe(monkeypatch, tmp_path):
    explicit = executable(tmp_path / "explicit")
    configured = executable(tmp_path / "configured")
    monkeypatch.setenv("PDFCRAFT_CLI", str(configured))
    seen = []
    monkeypatch.setattr(host, "cli_version", lambda path: seen.append(path))
    status = host.probe(explicit)
    assert status.binaries.cli == explicit.resolve()
    assert status.binaries.cli_source == "argument"
    assert seen == [explicit.resolve()]


def test_invalid_probe_path_does_not_probe_configured_or_discovered_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("PDFCRAFT_CLI", str(executable(tmp_path / "environment")))
    status = host.probe(tmp_path / "missing")
    assert status.binaries.cli is None
    assert status.binaries.cli_source == "argument"
    assert status.configuration_error


def test_doctor_cli_status_and_action_agree_with_configured_path(monkeypatch, tmp_path, capsys):
    configured = executable(tmp_path / "configured")
    monkeypatch.setenv("PDFCRAFT_CLI", str(configured))
    probed = []
    monkeypatch.setattr(host, "cli_version", lambda path: probed.append(path))
    doctor_report = doctor.doctor_report()
    assert doctor_report["headless_ready"]
    assert str(configured) in doctor_report["checks"]["cli"]["detail"]
    assert doctor_report["cli_location_hint"]["source"] == "environment"
    assert cli.main(["status"]) == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert str(configured) in cli_report["detail"]
    assert cli_report["cli_location_hint"]["binary_trust_verified"] is False
    assert actions.host_status()["result"]["available"]
    assert probed == [configured.resolve()] * 3


def test_action_uses_owning_server_constructor_path(monkeypatch, tmp_path):
    configured = executable(tmp_path / "environment")
    explicit = executable(tmp_path / "server")
    monkeypatch.setenv("PDFCRAFT_CLI", str(configured))
    seen = []
    monkeypatch.setattr(host, "cli_version", lambda path: seen.append(path))
    token = active_server.set(SimpleNamespace(cli_binary=str(explicit)))
    try:
        report = actions.host_status()["result"]
    finally:
        active_server.reset(token)
    assert str(explicit) in report["detail"]
    assert report["cli_location_hint"]["source"] == "argument"
    assert seen == [explicit.resolve()]


def test_transport_and_diagnostics_share_tilde_normalization(monkeypatch, tmp_path):
    expected = executable(tmp_path / "tools" / cli_name())
    assert os.access(expected, os.X_OK)
    expanduser = os.path.expanduser

    def fixture_expanduser(value):
        # Windows pathlib passes the home component; POSIX passes the full path.
        if value == "~":
            return str(tmp_path)
        if value.startswith(("~/", "~\\")):
            return str(tmp_path / value[2:])
        return expanduser(value)

    assert Path(fixture_expanduser("~/tools/" + cli_name())) == expected
    monkeypatch.setattr(os.path, "expanduser", fixture_expanduser)
    configured = str(Path("~") / "tools" / cli_name())
    monkeypatch.setenv("PDFCRAFT_CLI", configured)
    monkeypatch.setattr(host, "cli_version", lambda _: None)
    assert host.probe().binaries.cli == expected.resolve()
    assert CliTransport(configured).binary == str(expected.resolve())
    assert capability_dispatch._transport().binary == str(expected.resolve())


@pytest.mark.skipif(os.name == "nt", reason="Windows executable access differs from POSIX mode bits")
def test_nonexecutable_explicit_file_is_unusable_everywhere(monkeypatch, tmp_path):
    configured = tmp_path / "not-executable"
    configured.write_bytes(b"not executed")
    configured.chmod(0o600)
    monkeypatch.setenv("PDFCRAFT_CLI", str(configured))
    assert not host.probe().cli_available
    with pytest.raises(CliNotFound):
        CliTransport(configured)
    with pytest.raises(CliNotFound):
        capability_dispatch._transport()


def test_portable_data_override_and_platform_default(monkeypatch, tmp_path):
    # Preserve every other isolation stub while exercising the actual root list.
    monkeypatch.setattr(launcher, "data_directories", _DATA_DIRECTORIES)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setenv("PDFCRAFT_LAUNCHER_DATA", str(tmp_path / "portable"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert launcher.data_directories()[:2] == [tmp_path / "portable", tmp_path / "xdg/artcraft-launcher"]
    monkeypatch.setenv("PDFCRAFT_LAUNCHER_DATA", "relative")
    assert launcher.data_directories()[0] == tmp_path / "xdg/artcraft-launcher"


_DATA_DIRECTORIES = launcher.data_directories
