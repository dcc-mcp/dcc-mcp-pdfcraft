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

What it deliberately does **not** do is reimplement PdfCraft's command set. Tool ids are resolved at runtime against the table the installed host reports, so capability discovery and argument validation follow the installed host. Document writes use a deliberately reviewed subset; discovering a tool does not grant permission to execute it.

## Requirements

- Python 3.9 or newer; DCC-MCP Core 0.20.44 through the tested 0.20.x line
- [PdfCraft](https://github.com/storytold/pdfcraft) 0.5 or newer — only `pdfcraft-cli` is required; the desktop app is optional

## Installation

PyPI publication is not yet confirmed. For a reviewed source checkout:

```bash
python -m pip install .
```

PdfCraft has no embedded Python. Select the interpreter through the existing
DCC-MCP Runtime/launcher configuration; this adapter does not install or switch
a shared Python runtime. The current draft retains version 0.2.0 but differs
from tag `v0.2.0`; it must not replace that tag's artifacts under the same
published version. A future release needs its own reviewed version/source.

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
| `PDFCRAFT_ROOT` | Existing document directory; required for native batches and forwarded as `run --root` |
| `PDFCRAFT_LAUNCHER_DATA` | Optional absolute path to an existing Craft Launcher data directory |

CLI selection consistently prefers an explicit constructor path, then
`PDFCRAFT_CLI`, PATH, official Craft Launcher native location hints and platform
installation directories. An invalid configured path is reported rather than
silently replaced. Status, doctor and actual calls use the same path resolution.
Launcher discovery reads a bounded `installed.json`; it does not install,
update, unpack AppImages or prove binary provenance. Filesystem discovery does
not execute a host; the separate version probe invokes the selected CLI.

### Headless and live-UI paths

Most actions run through `pdfcraft-cli` and need no window, which is what makes them work on a headless server and in CI. The UI actions (`ui_screenshot`, `ui_inspect`) additionally need the desktop app started with:

```bash
pdfcraft --control /path/to/control.json
```

PdfCraft then listens on a random loopback port and writes that port and an auth token into the file. Point `PDFCRAFT_CONTROL_FILE` at it. The port is random per start, so the adapter re-reads the file on each call — there is no fixed port to collide on, which matters because the sibling Craft apps all use loopback control channels too.

The external adapter service is explicitly registered as `standalone`, with no
bound desktop PID. Its lifetime is independent of an optional editor window.
The secondary gateway listener defaults to loopback and an OS-selected port.
Location hints and a configured control file do not establish a live GUI
connection or grant desktop access.

## Native document workflows through Core MCP

Load the packaged `pdfcraft-core` skill, then discover its namespaced actions. `list_pdfcraft_tools` reports the installed CLI's actual schemas. `execute_pdfcraft_batch` runs up to 64 reviewed document operations in **one native process**, preserving document IDs between edits. IDs expire when that batch ends; reopen a saved PDF in the next batch.

Supported batch operations are create/open/close/save/info, metadata edits, adding images and text, rendering, undo and redo. Each argument set is validated against the live host schema. JavaScript execution, signing, encryption and arbitrary upstream commands are outside this batch API. The Core server retains its existing management/introspection tools; this subset is not a restriction on every Core facility.

Set `PDFCRAFT_ROOT` to a dedicated local work directory before starting. Inputs must resolve inside it. Saves require new `.pdf` names; renders require new `.png` names. Outputs are staged and published without replacement. On an uncertain or partial failure, inspect the reported paths before deciding what to do; do not automatically replay a write batch. See [the workflow guide](docs/native-storyboard.md) for bounds, platform coverage and the reproducible case.

Native batch **file publication requires POSIX descriptor-relative, no-follow support**, verified on Linux. Unsupported platforms fail before execution when a batch requests output files. Host detection, discovery and existing read/UI paths remain separate capabilities. The desktop control channel and browser UI are not covered by this native acceptance case.

### FilmCraft → PdfCraft showcase

[Time, held twice](docs/native-storyboard.md) takes three frames from a real, half-speed FilmCraft MP4 and makes an editable two-page PdfCraft review sheet. The opt-in CI job recreates both applications' work through MCP, saves and reopens the native documents, and uploads the PDF, previews and machine-readable evidence. Independent checks compare every embedded frame pixel and render the resulting PDF with Poppler.

The current native CI fixtures target official FilmCraft 0.12.0 and PdfCraft
0.13.0. Their archive and executable hashes are checked before execution; these
pins are acceptance provenance, not an exact-version restriction on users.
The older browser fixture is documented as historical evidence separately.

### PhotoCraft / FilmCraft → PdfCraft asset handoff

The [asset contract](docs/asset-handoff-contract.md) reuses two already qualified
PhotoCraft 0.15.1 RGB/RGBA test images and a frame decoded from the actual
FilmCraft export. It saves a three-page native PDF, then opens and renders it in
a second fresh native batch. Independent checks inspect PDF geometry, numeric
RGB image data, alpha soft-mask bytes and white-composited previews. Original
fixtures and their producer hashes are retained; the PhotoCraft producer and
the existing Nuke tests are not rerun by this case.

Untagged RGB remains DeviceRGB; this does not establish ICC assignment, OCIO or
scene-linear processing. The report distinguishes PDF soft-mask preservation
from the renderer's opaque white background. It does not establish a complete
multi-consumer contract or native desktop control.

The optional `scripts/native_readback.py` can verify a hash-selected official
CLI through real Core MCP without publishing files. This separate read-only
path works on Windows; Windows native output publication remains unavailable.

## Host maturity

PdfCraft is pre-1.0 software. Its tool table can change; discover capabilities and schemas from the installed host. The pinned official host versions in the acceptance workflow are reproducible test fixtures, not exact-version restrictions in the adapter.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
ruff check src tests
ruff format --check src tests
```

## License

MIT — see [LICENSE](LICENSE).
