"""Shared fixtures for the PdfCraft adapter test suite.

The tests never require PdfCraft to be installed, and they never reach the
network. The loopback control channel is exercised against a real socket
server started in-process, because that is the only way to prove the
authentication handshake and the line framing are correct rather than merely
self-consistent.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Any

import pytest

# A token used only by the in-process fake server.
TEST_TOKEN = "test-token-value"


class FakeControlServer:
    """A minimal stand-in for PdfCraft's authenticated control server.

    It reproduces the three behaviours the adapter depends on: the bind is
    loopback on an OS-chosen port, the first frame must be ``auth`` with the
    right token, and each reply is one line of JSON-RPC 2.0.
    """

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = self._listener.getsockname()[1]
        self.received: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="fake-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        try:
            self._listener.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        stream = connection.makefile("rb")
        authed = False
        try:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._write(
                        connection,
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "parse error"},
                        },
                    )
                    continue

                method = message.get("method", "")
                params = message.get("params") or {}
                request_id = message.get("id")

                if not authed:
                    if method == "auth" and params.get("token") == TEST_TOKEN:
                        authed = True
                        self._write(connection, {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}})
                    else:
                        self._write(
                            connection,
                            {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {"code": -32001, "message": "authenticate first"},
                            },
                        )
                        return
                    continue

                self.received.append({"method": method, "params": params})
                self._write(
                    connection,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"echo": method, "params": params},
                    },
                )
        except OSError:
            pass
        finally:
            try:
                stream.close()
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _write(connection: socket.socket, payload: dict[str, Any]) -> None:
        try:
            connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        except OSError:
            pass


@pytest.fixture
def fake_server():
    """Run a fake control server for the duration of one test."""
    server = FakeControlServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def control_file(tmp_path, fake_server):
    """Write a control file pointing at the fake server, as PdfCraft would."""
    path = tmp_path / "pdfcraft.control"
    path.write_text(json.dumps({"port": fake_server.port, "token": TEST_TOKEN}), encoding="utf-8")
    yield path


@pytest.fixture(autouse=True)
def isolated_core_runtime(monkeypatch, tmp_path):
    """Never discover user skills or persist telemetry/jobs while testing."""
    for name in (
        "DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS",
        "DCC_MCP_DISABLE_ACCUMULATED_SKILLS",
        "DCC_MCP_DISABLE_FILE_LOGGING",
        "DCC_MCP_DISABLE_JOB_PERSISTENCE",
        "DCC_MCP_DISABLE_TELEMETRY",
        "DCC_MCP_CHECKPOINT_IN_MEMORY",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_CORE_VERSION_CHECK", "0")
    for name in ("DCC_MCP_SKILL_PATHS", "DCC_MCP_PDFCRAFT_SKILL_PATHS"):
        monkeypatch.delenv(name, raising=False)
