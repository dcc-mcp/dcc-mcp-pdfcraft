# dcc-mcp-pdfcraft

[![PyPI version](https://img.shields.io/pypi/v/dcc-mcp-pdfcraft.svg)](https://pypi.org/project/dcc-mcp-pdfcraft/)
[![Python versions](https://img.shields.io/pypi/pyversions/dcc-mcp-pdfcraft.svg)](https://pypi.org/project/dcc-mcp-pdfcraft/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

PdfCraft adapter for the [DCC-MCP](https://github.com/dcc-mcp/dcc-mcp-core) ecosystem: lets AI agents inspect and drive [PdfCraft](https://github.com/storytold/pdfcraft), the open-source PDF workstation.

## Why this adapter exists when PdfCraft already ships an MCP server

**PdfCraft includes its own MCP server** (`pdfcraft-cli mcp`), and its documentation shows how to register it directly with an agent. If that is all you need, use it — this package does not replace it.

This adapter adds the things a standalone MCP server does not provide:

- **One control plane across every DCC.** PdfCraft becomes one entry in the same tool registry as Maya, Blender, Houdini, Nuke and the rest, so an agent discovers and calls it through the same `dcc-mcp-cli` commands and the same gateway instead of managing a second set of per-app connections.
- **Cross-DCC orchestration.** A workflow can move from PdfCraft to another host in one session — for example, extracting pages here and compositing them elsewhere.
- **Sidecar lifecycle and discovery.** The adapter registers the running instance, reports readiness, and exposes a doctor that distinguishes "PdfCraft is not installed" from "PdfCraft is installed but misconfigured".
- **Host detection without Rust.** It finds the `pdfcraft-cli` binary whether it arrived inside a GUI package or as a standalone archive, on Windows, macOS and Linux.

What it deliberately does **not** do is reimplement PdfCraft's command set. Tool ids are resolved at runtime against the table the installed host reports, so new upstream tools become reachable without an adapter release.

## Requirements

- Python 3.9 or newer
- [PdfCraft](https://github.com/storytold/pdfcraft) 0.5 or newer — only `pdfcraft-cli` is required; the desktop app is optional

## Installation

```bash
pip install dcc-mcp-pdfcraft
```

Verify the host is reachable:

```bash
dcc-mcp-pdfcraft-install
```

## Usage

Run the adapter:

```bash
dcc-mcp-pdfcraft
```

Report the detected host:

```bash
dcc-mcp-pdfcraft status
```

Ask your agent to inspect a document:

```text
Use dcc-mcp to summarize contract.pdf.
```

### Configuration

| Variable | Purpose |
| --- | --- |
| `PDFCRAFT_CLI` | Path to `pdfcraft-cli` when it is not on `PATH` |
| `PDFCRAFT_CONTROL_FILE` | Path to the file written by `pdfcraft --control <file>`; enables UI actions |
| `PDFCRAFT_ROOT` | Sandbox root forwarded to `pdfcraft-cli --root` |

### Headless and live-UI paths

Most actions run through `pdfcraft-cli` and need no window, which is what makes them work on a headless server and in CI. The UI actions (`ui_screenshot`, `ui_inspect`) additionally need the desktop app started with:

```bash
pdfcraft --control /path/to/control.json
```

PdfCraft then listens on a random loopback port and writes that port and an auth token into the file. Point `PDFCRAFT_CONTROL_FILE` at it. The port is random per start, so the adapter re-reads the file on each call — there is no fixed port to collide on, which matters because the sibling Craft apps all use loopback control channels too.

## Host maturity

PdfCraft is **pre-1.0 software**. Its release engineering is mature — signed installers, automated releases, a shipped CLI — but upstream describes its own product readiness as early beta and overall progress as roughly a third of the effort. Expect the tool table and command set to keep changing; discover them at runtime rather than relying on a fixed list.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
ruff check src tests
ruff format --check src tests
```

## License

MIT — see [LICENSE](LICENSE).
