---
name: pdfcraft-core
description: >-
  Domain skill — inspect PDF documents, discover the tool and command tables the
  installed PdfCraft publishes, and drive the running PdfCraft desktop app.
license: MIT
compatibility: "PdfCraft 0.5+; dcc-mcp-core 0.20.36+"
allowed-tools: "python"
metadata:
  dcc-mcp:
    dcc: pdfcraft
    layer: domain
    version: "0.1.0"
    search-hint: "pdfcraft pdf_info list_pdfcraft_tools list_pdfcraft_commands ui_screenshot ui_inspect"
    tags: "pdfcraft,pdf,documents"
    tools: tools.yaml
---

# PdfCraft Core

Inspect and drive [PdfCraft](https://github.com/storytold/pdfcraft), the
open-source PDF workstation.

## Headless first

`pdf_info`, `list_pdfcraft_tools` and `list_pdfcraft_commands` run through
`pdfcraft-cli` and need no window, display server, or running app. This is the
path to prefer: it is the only one that works in CI and on a headless server.

## Discover, do not assume

PdfCraft is pre-1.0 and ships a new minor version every one to two days, and its
tool table grows continuously. **Call `list_pdfcraft_tools` before assuming a
tool exists.** The tool ids accepted by this skill are resolved against the
table the installed host reports, not against a list frozen at adapter release
time, so a tool that appears upstream becomes reachable without an adapter
release.

## Driving the live app

`ui_screenshot` and `ui_inspect` need the desktop app started with
`--control <file>`, which makes it listen on a random loopback port and write
that port plus an auth token into the file. Point `PDFCRAFT_CONTROL_FILE` at it
before calling either. When no instance is attached these actions fail with
guidance rather than returning an empty result.

The port is random per start, so after restarting the app the adapter re-reads
the control file on the next call; there is no fixed port to collide on, which
matters because the sibling Craft apps all use loopback control channels too.
