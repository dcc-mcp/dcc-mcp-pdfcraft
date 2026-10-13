---
name: pdfcraft-core
description: >-
  Inspect PDF documents, discover installed PdfCraft capabilities, and create
  root-confined document batches with native save, reopen and render checks.
license: MIT
compatibility: "PdfCraft native CLI with the required discovered tools; dcc-mcp-core >=0.20.44,<0.21.0"
allowed-tools: "python"
metadata:
  dcc-mcp:
    dcc: pdfcraft
    layer: domain
    version: "0.1.0"
    search-hint: "pdfcraft pdf_info list_pdfcraft_tools list_pdfcraft_commands execute_pdfcraft_batch storyboard"
    tags: "pdfcraft,pdf,documents,storyboard"
    tools: tools.yaml
---

# PdfCraft Core

Inspect and drive [PdfCraft](https://github.com/storytold/pdfcraft), the
open-source PDF workstation. Load this skill through Core's `load_skill`, then
call the returned namespaced tools such as
`pdfcraft_core__execute_pdfcraft_batch`.

## Discover before use

`list_pdfcraft_tools` returns the installed host's live tool table and schemas.
`list_pdfcraft_commands` derives engine command mappings from that table.
Discovery does not authorize every upstream tool: execution is restricted to
the reviewed document workflow below. No frozen copy of upstream schemas is
shipped; every batch is checked against the currently installed host.

`pdf_info` reads a saved PDF without a running desktop app. When a root is
configured, the path must remain inside that root.

## One native document session

A separate CLI invocation starts a fresh document session. Keep related edits
inside one `execute_pdfcraft_batch` call, with a `steps` array of
`{"tool": "doc_create", "args": {"from": "blank"}}`-shaped entries. Document
IDs only have meaning within that batch. The returned `result.steps` preserves
one native result for each submitted step.

The reviewed subset is `doc_create`, `doc_open`, `doc_close`, `doc_save`,
`doc_info`, `doc_set_info`, `page_add_image`, `page_add_text`, `page_render`,
`edit_undo`, and `edit_redo`. JavaScript, signing, security changes and other
upstream tools are not accepted by the batch action.

Configure `PDFCRAFT_ROOT` or the server's `root` constructor argument before
executing a batch. Paths may be absolute inside that root or relative to it.
Symlink and traversal escapes are rejected. Input files must exist and be at
most 64 MiB. Every step is validated before the native process starts.

A batch accepts at most 64 steps and 1 MiB of JSON. New documents are limited
to 100 pages, 100 image inputs, and 2000-point dimensions. Rendering is limited
to 144 dpi. These adapter limits are stricter than some host schemas.

## Save, reopen, render, verify

Always give `doc_save` a new `.pdf` path. In-place saves and existing outputs
are refused. Give `page_render` a top-level `out` field naming a new `.png`
path; image bytes are not returned inline. Output parent directories must
already exist.

Batch file publication requires descriptor-relative, no-follow filesystem
operations. It is supported on POSIX platforms exposing those operations and
is exercised against the native host on Linux. Windows publication currently
fails closed before native execution; discovery and read-only inspection
remain available. No native Windows or macOS host acceptance is claimed.

Outputs are staged within the root and published without replacement only
after the native batch succeeds. A later `doc_open` in the same batch can
read an earlier save before publication. Published outputs include byte sizes
and SHA-256 checksums. Each output is limited to 128 MiB.

For editing acceptance, save, close, reopen, inspect metadata/page count, and
render the reopened document. Independently inspect the resulting PDF and
images when correctness matters; a successful host response alone does not
prove the visible result.

Do not automatically retry a failed or timed-out batch. Review the error and
filesystem first. A publication conflict can leave some completed outputs;
the error names those already published. The entire batch is not a transaction.

## Driving the live app

`ui_screenshot` and `ui_inspect` need the desktop app started with
`--control <file>`. It binds a random loopback port and writes the port plus
an auth token to the file. Configure `PDFCRAFT_CONTROL_FILE` or the server's
`control_file` constructor argument. The token is never included in diagnostics.
The endpoint is read again for each action so an app restart does not leave a
cached dead port. Desktop behavior is separate from the headless CLI workflow.
