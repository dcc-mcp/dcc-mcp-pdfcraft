"""Loopback JSON-lines client for a running PdfCraft instance.

PdfCraft exposes two automation surfaces that a Python adapter can drive
without a Rust toolchain:

* ``pdfcraft-cli`` -- a headless subprocess. Every engine command is one shell
  call away, and no window is needed.
* ``pdfcraft --control <file>`` -- a control channel that drives the *running*
  desktop app. The app binds ``127.0.0.1`` on a random port, writes that port
  and a random token to the file passed to ``--control``, and requires an
  ``auth`` frame before any other request.

This module implements the second surface. It is deliberately dependency-free:
the wire format is newline-delimited JSON-RPC 2.0 over a loopback socket, so
the standard library is enough. Keeping it free of a third-party MCP SDK means
the adapter does not inherit that SDK's Python floor or its release cadence,
and it does not have to track upstream protocol revisions just to forward a
line of JSON.

The port and token are read from the control file rather than configured, so
two PdfCraft instances -- or PdfCraft alongside the six sibling Craft apps --
can never collide on a fixed port.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# PdfCraft's control server answers each request within 30 seconds and closes
# the connection when a request is not authenticated. Mirroring the bound here
# keeps a stalled app from hanging the adapter indefinitely.
DEFAULT_TIMEOUT_SECS = 30.0

# Error codes returned by PdfCraft's control server, from
# ``crates/ui-egui/src/control.rs``.
AUTH_REQUIRED = -32001
APP_TIMEOUT = -32002

HOST = "127.0.0.1"


class ControlChannelError(RuntimeError):
    """A control-channel request could not be completed."""


class ControlChannelUnavailable(ControlChannelError):
    """The control channel is absent, unreachable, or not authenticated."""


@dataclass(frozen=True)
class Endpoint:
    """The loopback address and token PdfCraft wrote to its control file."""

    port: int
    token: str


def read_endpoint(control_file: str | Path) -> Endpoint:
    """Read the port and token that a running PdfCraft wrote to ``control_file``.

    The file is written by the app itself when it is started with
    ``--control <file>``, so a missing or malformed file means the app is not
    running in control mode rather than that a configuration is wrong.
    """
    path = Path(control_file)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControlChannelUnavailable(f"cannot read control file {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControlChannelUnavailable(f"control file {path} is not JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ControlChannelUnavailable(f"control file {path} is not a JSON object")

    port_value = payload.get("port")
    token = payload.get("token")
    if not isinstance(port_value, int) or isinstance(port_value, bool):
        raise ControlChannelUnavailable(f"control file {path} has no usable port")
    if not isinstance(token, str) or not token:
        raise ControlChannelUnavailable(f"control file {path} has no usable token")

    # Refuse rather than truncate: a port above the valid range would silently
    # reach some other service.
    if not 0 < port_value < 65536:
        raise ControlChannelUnavailable(f"control file {path} has out-of-range port {port_value}")

    return Endpoint(port=port_value, token=token)


class ControlChannel:
    """A newline-delimited JSON-RPC 2.0 client for one PdfCraft instance.

    Connections are opened lazily per request and authenticated on connect.
    PdfCraft spawns a thread per connection and answers requests sequentially,
    so a short-lived connection per call is the simplest correct shape and
    avoids holding a slot in its 16-connection limit between calls.
    """

    def __init__(self, endpoint: Endpoint, *, timeout_secs: float = DEFAULT_TIMEOUT_SECS) -> None:
        self._endpoint = endpoint
        self._timeout_secs = timeout_secs
        self._request_id = 0

    @property
    def port(self) -> int:
        return self._endpoint.port

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one request and return its ``result``, or raise on ``error``."""
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params if params is not None else {},
        }

        try:
            with socket.create_connection((HOST, self._endpoint.port), timeout=self._timeout_secs) as connection:
                # ``makefile`` is used instead of ``recv`` so that a reply split
                # across TCP segments is still read as one line.
                stream = connection.makefile("rb")
                try:
                    # The first frame on every connection must be the auth
                    # request; PdfCraft closes the connection otherwise.
                    self._send(connection, "auth", {"token": self._endpoint.token}, request_id=0)
                    self._receive(stream, request_id=0)
                    self._send(connection, method, request["params"], request_id=self._request_id)
                    return self._receive(stream, request_id=self._request_id)
                finally:
                    stream.close()
        except OSError as exc:
            raise ControlChannelUnavailable(f"cannot reach PdfCraft on {HOST}:{self._endpoint.port}: {exc}") from exc

    @staticmethod
    def _send(
        connection: socket.socket,
        method: str,
        params: dict[str, Any],
        *,
        request_id: int,
    ) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        connection.sendall(payload.encode("utf-8") + b"\n")

    def _receive(self, stream: Any, *, request_id: int) -> Any:
        line = stream.readline()
        if not line:
            raise ControlChannelUnavailable("PdfCraft closed the connection without answering")
        try:
            reply = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControlChannelError(f"PdfCraft sent a malformed reply: {exc}") from exc

        if "error" in reply:
            error = reply["error"]
            message = error.get("message", "unknown error") if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else None
            if code == AUTH_REQUIRED:
                raise ControlChannelUnavailable(f"authentication refused: {message}")
            if code == APP_TIMEOUT:
                raise ControlChannelUnavailable(f"PdfCraft did not answer in time: {message}")
            raise ControlChannelError(f"{message} (code {code})")

        if reply.get("id") != request_id:
            raise ControlChannelError(f"reply id {reply.get('id')!r} does not match request id {request_id!r}")
        return reply.get("result")
