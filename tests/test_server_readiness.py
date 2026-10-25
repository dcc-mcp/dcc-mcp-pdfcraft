"""Exercise the released Core readiness contract instead of stubbing it."""

from dcc_mcp_pdfcraft import server as server_module


def test_external_service_has_standalone_identity_and_disabled_secondary_listener(monkeypatch, tmp_path):
    captured = []
    original = server_module.DccServerOptions.from_env

    def capture(*args, **kwargs):
        options = original(*args, **kwargs)
        captured.append(options)
        return options

    monkeypatch.setattr(server_module.DccServerOptions, "from_env", capture)
    instance = server_module.PdfCraftMcpServer(
        registry_dir=str(tmp_path / "registry"),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_telemetry=False,
    )
    try:
        assert captured[0].instance_type == "standalone"
        assert captured[0].gateway.remote_host == "127.0.0.1"
        # Core defines zero as disabled, not an OS-assigned secondary port.
        assert captured[0].gateway.remote_port == 0
    finally:
        instance.stop()


def test_start_uses_real_core_readiness_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module.PdfCraftMcpServer, "_host_ready", lambda self: True)
    calls = []
    monkeypatch.setattr(server_module.DccServerBase, "start", lambda self, **kw: calls.append("start"))
    monkeypatch.setattr(server_module.DccServerBase, "stop", lambda self, **kw: calls.append("stop"))
    instance = server_module.PdfCraftMcpServer(
        gateway_port=0,
        registry_dir=str(tmp_path),
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_telemetry=False,
    )
    try:
        instance.start()
        assert calls == ["start"]
        assert instance._readiness.server is instance
    finally:
        instance.stop()


def test_failed_start_does_not_cache_singleton(monkeypatch):
    monkeypatch.setattr(server_module, "_server", None)

    def fail(self):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(server_module.PdfCraftMcpServer, "start", fail)
    import pytest

    with pytest.raises(RuntimeError, match="startup failed"):
        server_module.start_server()
    assert server_module._server is None


def test_startup_failure_stops_dispatcher(monkeypatch, tmp_path):
    def fail(self, **kwargs):
        raise RuntimeError("listener failed")

    monkeypatch.setattr(server_module.DccServerBase, "start", fail)
    instance = server_module.PdfCraftMcpServer(
        gateway_port=0,
        registry_dir=str(tmp_path),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_telemetry=False,
    )
    import pytest

    with pytest.raises(RuntimeError, match="listener failed"):
        instance.start()
    assert not instance._host_driver.is_running


def test_real_http_catalog_dispatch_and_shutdown(monkeypatch, tmp_path):
    """Use the released Core listener and skill loader; only the host is fake."""
    import json
    import socket
    import urllib.request

    from dcc_mcp_pdfcraft.cli_transport import CliTransport

    binary = tmp_path / "configured-cli"
    binary.touch()
    binary.chmod(binary.stat().st_mode | 0o111)
    seen = []

    def tools(transport):
        seen.append((transport.binary, transport.root))
        return [
            {
                "name": "doc_info",
                "command": "file.properties",
                "input_schema": {
                    "type": "object",
                    "properties": {"doc": {"type": "integer", "minimum": 1}},
                    "required": ["doc"],
                    "additionalProperties": False,
                },
            }
        ]

    def run(transport, args):
        from pathlib import Path

        assert args[:2] == ["run", "--script"]
        assert json.loads(Path(args[2]).read_text()) == [{"tool": "doc_info", "args": {"doc": 1}}]
        return '{"pages":2}'

    monkeypatch.setattr(CliTransport, "tools", tools)
    monkeypatch.setattr(CliTransport, "run", run)
    monkeypatch.setenv("PDFCRAFT_CLI", "wrong-environment-binary")
    monkeypatch.setenv("PDFCRAFT_ROOT", "wrong-environment-root")
    instance = server_module.PdfCraftMcpServer(
        port=0,
        gateway_port=0,
        registry_dir=str(tmp_path / "registry"),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_checkpoint_tools=False,
        enable_telemetry=False,
        cli_binary=str(binary),
        root=str(tmp_path),
    )
    session = None
    request_id = 0

    def rpc(method, params):
        nonlocal session, request_id
        request_id += 1
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        request = urllib.request.Request(
            handle.mcp_url(),
            headers=headers,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            ).encode(),
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            session = response.headers.get("Mcp-Session-Id", session)
            return json.load(response)["result"]

    def call(name, arguments=None, *, error=False):
        result = rpc("tools/call", {"name": name, "arguments": arguments or {}})
        assert result.get("isError", False) is error, result
        if error:
            return result
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    try:
        handle = instance.start(install_atexit_hook=False)
        assert instance.start(install_atexit_hook=False) is handle
        assert handle.port > 0 and handle.mcp_url().startswith("http://127.0.0.1:")
        initialized = rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pdfcraft-regression", "version": "1"},
            },
        )
        assert initialized["serverInfo"]["name"] == "dcc-mcp-pdfcraft"
        skills = call("list_skills")
        assert any(row["name"] == "pdfcraft-core" for row in skills["skills"])
        loaded = call("load_skill", {"skill_name": "pdfcraft-core"})
        assert loaded["loaded"] is True and loaded["tool_count"] == 6
        names = set(loaded["registered_tools"])
        assert "pdfcraft_core__execute_pdfcraft_batch" in names
        assert call("pdfcraft_core__list_pdfcraft_tools")["tools"][0]["name"] == "doc_info"
        assert call("pdfcraft_core__list_pdfcraft_commands")["commands"] == [
            {"id": "file.properties", "tools": ["doc_info"]}
        ]
        result = call("pdfcraft_core__execute_pdfcraft_batch", {"steps": [{"tool": "doc_info", "args": {"doc": 1}}]})
        assert result["result"]["steps"] == [{"pages": 2}]
        call("pdfcraft_core__execute_pdfcraft_batch", {"steps": [{"tool": "js_run", "args": {}}]}, error=True)
        call(
            "pdfcraft_core__execute_pdfcraft_batch",
            {"steps": [{"tool": "doc_info", "args": {"doc": True}}]},
            error=True,
        )
        assert seen and all(item == (str(binary), str(tmp_path)) for item in seen)
    finally:
        instance.stop()
    assert not instance._host_driver.is_running
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", handle.port)) != 0


def test_readiness_failure_stops_dispatcher(monkeypatch, tmp_path):
    import pytest

    def fail(*args, **kwargs):
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(server_module.AdapterReadinessBinder, "bind_queue_dispatcher", fail)
    instance = server_module.PdfCraftMcpServer(
        gateway_port=0,
        registry_dir=str(tmp_path),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_telemetry=False,
    )
    with pytest.raises(RuntimeError, match="readiness failed"):
        instance.start()
    assert not instance._host_driver.is_running
    with pytest.raises(RuntimeError, match="create a new"):
        instance.start()


def test_failed_stop_clears_singleton(monkeypatch):
    import pytest

    class FailedServer:
        def stop(self):
            raise RuntimeError("stop failed")

    monkeypatch.setattr(server_module, "_server", FailedServer())
    with pytest.raises(RuntimeError, match="stop failed"):
        server_module.stop_server()
    assert server_module._server is None
