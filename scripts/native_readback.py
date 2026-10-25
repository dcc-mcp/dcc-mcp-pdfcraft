"""Opt-in, hash-gated read-only Core MCP proof; never opens a desktop window."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
from pathlib import Path

from native_storyboard_acceptance import McpClient


def minimal_pdf() -> bytes:
    """Original blank input fixture; this is not claimed as a native export."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 48] /Contents 4 0 R >>",
        b"<< /Length 4 >>\nstream\nq Q\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + item + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def validate_one_page_readback(doc_info, pdf_info):
    """Reject successful native calls whose readback does not match the fixture."""
    pages = doc_info.get("pages") if isinstance(doc_info, dict) else None
    if not isinstance(pages, list) or len(pages) != 1:
        raise AssertionError("doc_info must report exactly one page")
    if not isinstance(pages[0], dict) or type(pages[0].get("page")) is not int or pages[0]["page"] != 1:
        raise AssertionError("doc_info must identify fixture page 1")
    count = pdf_info.get("pages") if isinstance(pdf_info, dict) else None
    if type(count) is not int or count != 1:
        raise AssertionError("pdf_info must report exactly one page")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdfcraft-cli", type=Path, required=True)
    parser.add_argument("--expected-cli-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "checks": [], "gui_validated": False, "output_publication_executed": False}

    def check(condition, name, detail=None):
        report["checks"].append({"ok": bool(condition), "name": name, "detail": detail})
        if not condition:
            raise AssertionError(name)

    instance, port = None, None
    try:
        binary = args.pdfcraft_cli.resolve()
        check(binary.is_file(), "configured official CLI exists")
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        check(digest == args.expected_cli_sha256, "CLI hash matches operator provenance")
        report["cli_sha256"] = digest
        root = args.output / "root"
        root.mkdir()
        source = root / "original-blank.pdf"
        source.write_bytes(minimal_pdf())
        before = hashlib.sha256(source.read_bytes()).hexdigest()

        from dcc_mcp_core import __version__ as core_version

        from dcc_mcp_pdfcraft.batch import secure_publication_supported
        from dcc_mcp_pdfcraft.cli_transport import CliTransport
        from dcc_mcp_pdfcraft.host import cli_version
        from dcc_mcp_pdfcraft.server import PdfCraftMcpServer

        report["core_version"] = core_version
        report["native_version"] = cli_version(binary)
        check(bool(report["native_version"]), "actual CLI reports its version")
        catalog = CliTransport(binary, root=root).tools()
        check(bool(catalog), "actual native tool schemas discovered")
        report["native_tool_count"] = len(catalog)
        instance = PdfCraftMcpServer(
            port=0,
            gateway_port=0,
            registry_dir=str(args.output / "registry"),
            enable_gateway_failover=False,
            enable_file_logging=False,
            enable_job_persistence=False,
            enable_telemetry=False,
            enable_checkpoint_persistence=False,
            enable_checkpoint_tools=False,
            cli_binary=str(binary),
            root=str(root),
        )
        handle = instance.start(install_atexit_hook=False)
        port = handle.port
        check(handle.mcp_url().startswith("http://127.0.0.1:"), "Core MCP listener is loopback")
        client = McpClient(handle.mcp_url())
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pdfcraft-native-readback", "version": "1"},
            },
        )
        client.request("notifications/initialized", {}, notification=True)
        check(initialized["serverInfo"]["name"] == "dcc-mcp-pdfcraft", "real Core MCP initialized")
        report["mcp_protocol"] = initialized["protocolVersion"]
        loaded = client.call("load_skill", {"skill_name": "pdfcraft-core"})
        check(loaded.get("loaded") is True, "packaged PdfCraft Skill loaded")
        names = [tool["name"] for tool in client.tools()]

        def name(suffix):
            matches = [item for item in names if item == suffix or item.endswith("__" + suffix)]
            check(len(matches) == 1, "typed action resolved: " + suffix)
            return matches[0]

        discovered = client.call(name("list_pdfcraft_tools"), {})
        check("doc_open" in json.dumps(discovered), "native schemas read through Core MCP")
        batch_name = name("execute_pdfcraft_batch")
        result = client.call(
            batch_name,
            {
                "steps": [
                    {"tool": "doc_open", "args": {"path": source.name}},
                    {"tool": "doc_info", "args": {"doc": 1}},
                    {"tool": "doc_close", "args": {"doc": 1}},
                ]
            },
        )
        detail = result["result"]
        check(len(detail["steps"]) == 3 and not detail["outputs"], "three real read-only native steps completed")
        report["native_readback"] = detail
        report["fresh_info"] = client.call(name("pdf_info"), {"path": source.name})
        validate_one_page_readback(detail["steps"][1], report["fresh_info"].get("result"))
        check(True, "doc_info and fresh pdf_info both read back exactly one page")
        check(hashlib.sha256(source.read_bytes()).hexdigest() == before, "input PDF bytes unchanged")
        report["secure_publication_supported"] = secure_publication_supported()
        report["passed"] = True
    except BaseException as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        raise
    finally:
        try:
            if instance is not None:
                instance.stop()
            if port:
                with socket.socket() as probe:
                    check(probe.connect_ex(("127.0.0.1", port)) != 0, "owned Core MCP listener closed")
        except BaseException as exc:
            report["passed"] = False
            report["cleanup_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            raise
        finally:
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}))


if __name__ == "__main__":
    main()
