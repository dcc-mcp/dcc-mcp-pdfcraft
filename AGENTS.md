# AGENTS.md — dcc-mcp-pdfcraft

PdfCraft adapter for the DCC Model Context Protocol. Navigation map for AI
agents, not a reference manual. Follow the links; do not read everything up
front.

## What this adapter is

PdfCraft has **no embedded Python interpreter**. Unlike the Maya, Blender or
Houdini adapters, this package never runs inside the host — it drives PdfCraft
as an external process over two surfaces:

* **`pdfcraft-cli`** — a headless subprocess. Primary path; no window, no
  display server, and the only path that works in CI.
* **The desktop app's control channel** — `pdfcraft --control <file>` binds a
  random loopback port, writes the port plus an auth token into that file, and
  requires an `auth` frame first. Used only for UI actions.

Both are optional. A machine with only the CLI installed is usable, not broken:
every headless capability works.

## The rule that matters most

**Do not hardcode the PdfCraft tool set.** Upstream ships a new minor version
every one to two days and its tool table grows continuously. Tool ids are
resolved at runtime against the table the installed host reports, via
`list_pdfcraft_tools` / `pdfcraft-cli tools`. A hardcoded list is stale on the
next upstream release.

The same applies to version assertions in tests: never assert an exact upstream
version.

## Build and test

```bash
python -m pip install -e ".[dev]"   # editable install with dev extras
python -m pytest                    # run the test suite
ruff check src tests                # lint
ruff format --check src tests       # format check (line-length 120, py39 target)
python -m build --no-isolation      # build wheel + sdist
```

Tests never require PdfCraft to be installed and never reach the network. The
control channel is exercised against an in-process fake server
(`tests/conftest.py`), so the auth handshake and line framing are proven against
a real socket rather than a mock.

## Layout

| Path | Purpose |
| --- | --- |
| `src/dcc_mcp_pdfcraft/env.py` | Environment variable names. A leaf module: the transports read config from here so they never import `server` back. |
| `src/dcc_mcp_pdfcraft/control_channel.py` | Loopback JSON-lines RPC client with the `auth` handshake. Dependency-free by design. |
| `src/dcc_mcp_pdfcraft/cli_transport.py` | `pdfcraft-cli` subprocess driver. |
| `src/dcc_mcp_pdfcraft/host.py` | Locating the binaries on PATH and in per-platform install directories. |
| `src/dcc_mcp_pdfcraft/capability_dispatch.py` | Routes skill actions to the right host surface. |
| `src/dcc_mcp_pdfcraft/actions.py` | Adapter-owned actions (`host_status`, `list_pdfcraft_tools`, …). |
| `src/dcc_mcp_pdfcraft/server.py` | `DccServerBase` composition and lifecycle. |
| `src/dcc_mcp_pdfcraft/doctor.py` | Read-only self-check. |
| `src/dcc_mcp_pdfcraft/skills/pdfcraft-core/` | Shipped skill: `SKILL.md` + `tools.yaml` + `scripts/dispatch.py`. |

## Conventions

* `requires-python` is `>=3.9`. The control-channel client is deliberately
  dependency-free so it does not inherit a third-party SDK's Python floor; the
  optional `mcp` extra is gated on `python_version >= "3.10"` for the same
  reason.
* PdfCraft's control channel has no fixed port — the port is random per app
  start and re-read from the control file on each call. Never introduce a
  fixed port; the six sibling Craft apps all use loopback control channels and
  would collide.
* The control token is a secret. Never log or echo it; the doctor reports only
  that one was present.
* Host detection is a filesystem lookup, never a subprocess spawn.
