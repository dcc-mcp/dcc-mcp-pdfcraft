"""Real MCP HTTP -> Core -> native PdfCraft, fed by a verified FilmCraft export."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from storyboard_case import make_case


class McpClient:
    def __init__(self, url):
        self.url, self.counter, self.session_id = url, 0, None

    def request(self, method, params, *, notification=False):
        self.counter += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = self.counter
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
            raw = response.read().decode()
        if notification and not raw:
            return None
        if raw.startswith("event:") or raw.startswith("data:"):
            raw = next(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
        result = json.loads(raw)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    def call(self, name, arguments):
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        if response.get("isError"):
            raise RuntimeError(response)
        if "structuredContent" in response:
            return response["structuredContent"]
        texts = [item["text"] for item in response.get("content", []) if item.get("type") == "text"]
        return json.loads(texts[0]) if texts else response

    def tools(self):
        found, cursor = [], None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            found.extend(result["tools"])
            cursor = result.get("nextCursor")
            if not cursor:
                return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdfcraft-cli", required=True)
    parser.add_argument("--filmcraft-head", default="1109592c164ebb4b124997ea2d9fd5eff7c5274c")
    args = parser.parse_args()
    args.output = args.output.resolve()
    from dcc_mcp_core import __version__ as core_version

    host_version = subprocess.check_output([args.pdfcraft_cli, "--version"], text=True).splitlines()[0]
    case = make_case(args.media, args.output, args.filmcraft_head, host_version, core_version)
    report = {"checks": [], "transport": "MCP HTTP", "host": "native PdfCraft CLI", "browser_coverage": False}

    def check(ok, name, detail=None):
        report["checks"].append({"ok": bool(ok), "name": name, "detail": detail})
        if not ok:
            raise AssertionError(name + ": " + str(detail))

    from dcc_mcp_pdfcraft.server import PdfCraftMcpServer

    report.update(
        core_version=core_version,
        native_version=host_version,
        media_info=case["media_info"],
        filmcraft_head=args.filmcraft_head,
        filmcraft_media_sha256=case["source_sha256"],
        steps_requested=len(case["steps"]),
    )
    with tempfile.TemporaryDirectory(prefix="pdfcraft-mcp-proof-") as registry:
        server = PdfCraftMcpServer(
            port=0,
            gateway_port=0,
            registry_dir=registry,
            enable_gateway_failover=False,
            enable_file_logging=False,
            enable_job_persistence=False,
            enable_telemetry=False,
            enable_checkpoint_persistence=False,
            enable_checkpoint_tools=False,
            cli_binary=args.pdfcraft_cli,
            root=str(args.output),
        )
        port = None
        try:
            handle = server.start()
            port = handle.port
            client = McpClient(handle.mcp_url())
            initial = client.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "filmcraft-storyboard-agent", "version": "1.0"},
                },
            )
            client.request("notifications/initialized", {}, notification=True)
            check(
                initial["serverInfo"]["name"] == "dcc-mcp-pdfcraft", "real MCP initialized", initial["protocolVersion"]
            )
            loaded = client.call("load_skill", {"skill_name": "pdfcraft-core"})
            report["skill_load"] = loaded
            tool_rows = client.tools()
            names = [row["name"] for row in tool_rows]

            def name(suffix):
                matches = [n for n in names if n == suffix or n.endswith("__" + suffix)]
                if len(matches) != 1:
                    raise AssertionError((suffix, matches))
                return matches[0]

            batch_name = name("execute_pdfcraft_batch")
            check(batch_name in names, "bounded native batch appears after skill loading")
            catalog = client.call(name("list_pdfcraft_tools"), {})
            report["host_catalog_response"] = catalog
            check("doc_create" in json.dumps(catalog), "actual host tool discovery succeeds")
            result = client.call(batch_name, {"steps": case["steps"]})
            check(len(result["result"]["steps"]) == len(case["steps"]), "all 47 native session steps returned results")
            check(len(result["result"]["outputs"]) == 3, "one native PDF and two native PNGs published")
            report["batch_response"] = result
            (args.output / "mcp-batch-response.json").write_text(json.dumps(result, indent=2) + "\n")
            check(case["pdf"].is_file(), "native PDF published through MCP")
            for page in (1, 2):
                check(
                    (args.output / f"native-page-{page}.png").is_file(),
                    f"reopened page {page} rendered by native PdfCraft",
                )
            info = client.call(name("pdf_info"), {"path": str(case["pdf"])})
            report["reopened_info"] = info
            check("Time, held twice" in json.dumps(info), "fresh native invocation reads saved document title")
            # All validation failures must occur before native execution or publication.
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()}
            for label, steps in [
                ("arbitrary JavaScript", [{"tool": "js_run", "args": {"code": "1 + 1"}}]),
                ("out-of-root read", [{"tool": "doc_open", "args": {"path": str(args.output.parent / "outside.pdf")}}]),
                (
                    "existing-file overwrite",
                    [
                        {"tool": "doc_create", "args": {"from": "blank"}},
                        {"tool": "doc_save", "args": {"doc": 1, "path": str(case["pdf"])}},
                    ],
                ),
                ("boolean document ID", [{"tool": "doc_info", "args": {"doc": True}}]),
            ]:
                try:
                    client.call(batch_name, {"steps": steps})
                except (RuntimeError, urllib.error.HTTPError):
                    check(True, label + " rejected")
                else:
                    check(False, label + " rejected")
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()}
            check(before == after, "negative calls preserve every input/output byte")
            check(not list(args.output.glob(".pdfcraft-batch-*")), "native staging directories cleaned")
        finally:
            server.stop()
            if port:
                with socket.socket() as probe:
                    check(probe.connect_ex(("127.0.0.1", port)) != 0, "MCP listener closes cleanly")
            (args.output / "mcp-report.json").write_text(json.dumps(report, indent=2) + "\n")
    info = subprocess.check_output(["pdfinfo", str(case["pdf"])], text=True)
    check(
        "Pages:           2" in info and "JavaScript:      no" in info,
        "independent PDF structure has 2 pages and no JavaScript",
    )
    text = subprocess.check_output(["pdftotext", str(case["pdf"]), "-"], text=True)
    for phrase in ("Time, held twice.", "00:00.833", "00:01.833", "PdfCraft"):
        check(phrase in text, "saved PDF retains text: " + phrase)
    subprocess.run(["pdftoppm", "-r", "96", "-png", str(case["pdf"]), str(args.output / "independent")], check=True)
    from verify_storyboard import verify

    report["pdf_oracles"] = verify(args.output)
    check(report["pdf_oracles"]["passed"], "embedded FilmCraft frame pixels and independent rendered PDF match")
    report["passed"] = True
    report["artifacts"] = {
        p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in args.output.iterdir()
        if p.is_file() and p.suffix in (".pdf", ".png", ".mp4")
    }
    (args.output / "mcp-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "checks": len(report["checks"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
