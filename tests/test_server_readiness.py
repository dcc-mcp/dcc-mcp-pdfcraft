"""Exercise the released Core readiness contract instead of stubbing it."""

from dcc_mcp_pdfcraft import server as server_module


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
