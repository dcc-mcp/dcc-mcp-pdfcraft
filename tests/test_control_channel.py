"""Tests for the loopback control-channel client.

These cover the contract that makes the channel safe to use: the auth
handshake happens before anything else, the port and token come from a file
rather than configuration, and malformed input produces a typed error instead
of a silent success.
"""

from __future__ import annotations

import json

import pytest

from dcc_mcp_pdfcraft import control_channel
from dcc_mcp_pdfcraft.control_channel import (
    ControlChannel,
    ControlChannelError,
    ControlChannelUnavailable,
    read_endpoint,
)

from .conftest import TEST_TOKEN


def test_read_endpoint_parses_port_and_token(tmp_path):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"port": 8123, "token": TEST_TOKEN}), encoding="utf-8")

    endpoint = read_endpoint(path)

    assert endpoint.port == 8123
    assert endpoint.token == TEST_TOKEN


def test_read_endpoint_rejects_missing_file(tmp_path):
    with pytest.raises(ControlChannelUnavailable):
        read_endpoint(tmp_path / "absent.json")


def test_read_endpoint_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ControlChannelUnavailable):
        read_endpoint(path)


def test_read_endpoint_rejects_out_of_range_port(tmp_path):
    path = tmp_path / "range.json"
    path.write_text(json.dumps({"port": 70000, "token": TEST_TOKEN}), encoding="utf-8")

    # Refused rather than truncated: a truncated port would silently reach some
    # other service.
    with pytest.raises(ControlChannelUnavailable):
        read_endpoint(path)


def test_read_endpoint_rejects_missing_token(tmp_path):
    path = tmp_path / "notoken.json"
    path.write_text(json.dumps({"port": 8123}), encoding="utf-8")

    with pytest.raises(ControlChannelUnavailable):
        read_endpoint(path)


def test_call_authenticates_then_returns_result(control_file, fake_server):
    channel = ControlChannel(read_endpoint(control_file))

    result = channel.call("ui.screenshot", {"page": 1})

    assert result == {"echo": "ui.screenshot", "params": {"page": 1}}
    # The auth frame is consumed by the channel, so only the real request is
    # visible to the app.
    assert [entry["method"] for entry in fake_server.received] == ["ui.screenshot"]


def test_call_reports_auth_failure_without_leaking(control_file):
    endpoint = read_endpoint(control_file)
    wrong = ControlChannel(control_channel.Endpoint(port=endpoint.port, token="wrong-token"))

    with pytest.raises(ControlChannelUnavailable) as excinfo:
        wrong.call("ui.state")

    assert "authentication refused" in str(excinfo.value)
    # The token must never appear in an error message.
    assert TEST_TOKEN not in str(excinfo.value)


def test_call_reports_unreachable_port(tmp_path):
    path = tmp_path / "dead.json"
    path.write_text(json.dumps({"port": 1, "token": TEST_TOKEN}), encoding="utf-8")

    with pytest.raises(ControlChannelUnavailable):
        ControlChannel(read_endpoint(path)).call("ui.state")


def test_call_raises_on_error_reply(control_file, fake_server, monkeypatch):
    # Force the fake server to answer with an application error so the error
    # branch is exercised against a real socket.
    def failing(connection):
        stream = connection.makefile("rb")
        try:
            for line in stream:
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                if message.get("method") == "auth":
                    connection.sendall(
                        json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": {"ok": True}}).encode() + b"\n"
                    )
                else:
                    connection.sendall(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": message.get("id"),
                                "error": {"code": -32002, "message": "the app did not answer"},
                            }
                        ).encode()
                        + b"\n"
                    )
        except OSError:
            pass

    monkeypatch.setattr(fake_server, "_handle", failing)

    with pytest.raises(ControlChannelUnavailable):
        ControlChannel(read_endpoint(control_file)).call("ui.state")


def test_call_detects_id_mismatch(control_file, fake_server, monkeypatch):
    def mismatched(connection):
        stream = connection.makefile("rb")
        try:
            for line in stream:
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                reply_id = 999 if message.get("method") != "auth" else message.get("id")
                connection.sendall(
                    json.dumps({"jsonrpc": "2.0", "id": reply_id, "result": {"ok": True}}).encode() + b"\n"
                )
        except OSError:
            pass

    monkeypatch.setattr(fake_server, "_handle", mismatched)

    with pytest.raises(ControlChannelError, match="does not match"):
        ControlChannel(read_endpoint(control_file)).call("ui.state")
