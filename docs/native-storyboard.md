# FilmCraft frames to a native PdfCraft storyboard

This original calibration study is titled **Time, held twice**. Its two-page layout contains three editorial beats, readable timecodes, editable PDF text, and a workflow receipt. All source footage and audio are procedural test material, with no third-party media.

## What actually runs

1. The paired job now targets the official FilmCraft native CLI in one owned persistent process. Genuine MCP calls create/import/edit a project, apply 50% speed, save and reopen `.fcproj`, and export native H.264/AAC MP4 and WAV. This path needs no browser or desktop window.
2. FFmpeg extracts output frames 0, 10 and 22. It performs extraction only; it does not author the PDF or replace FilmCraft's export.
3. A genuine MCP HTTP client initializes the PdfCraft Core server, loads `pdfcraft-core`, discovers the host schemas and invokes `execute_pdfcraft_batch`.
4. One PdfCraft CLI engine session runs 47 operations: create two pages, add native text/images, edit metadata, undo/redo, save, close, reopen, inspect and render both pages. A separate invocation reads the saved PDF's metadata.
5. Independent pypdf checks inspect the PDF structure and all four embedded images. Their decoded pixels must exactly match the extracted FilmCraft frames. Poppler checks text and renders both pages; its output must agree with native PdfCraft to mean absolute RGB error below 10/255.

This proves **native CLI/Core MCP integration** for PdfCraft. It does not establish browser editing, a desktop control-channel session, or document-ID persistence across CLI processes.

## Reproduce on Linux

The CI workflow `native-paired-workflow` contains the complete paired run. Its current qualification target uses FilmCraft adapter fixture commit `b2109ba45833438aadea935401e536dc6f74bc0e`, not a moving branch. This is reviewed draft source used as an acceptance fixture, not a released dependency or a claim that its publishing workflow is enabled. FilmCraft's helper pins its official native v0.12.0 archive and CLI. PdfCraft's helper independently pins the official [v0.13.0 CLI archive](https://github.com/storytold/pdfcraft/releases/tag/v0.13.0), release source `53199fb4fa36f3c95b539add6218c57aeb2f823c`:

- Archive SHA-256: `fd40f80157856cadd4348a464ef0acde8f19894d831d78e9823708ecf70a3352`, matching both the release API and `SHA256SUMS.txt`.
- Extracted CLI SHA-256: `2283260fc06a2cadc32c2836fcdd29ed2996418ab3c03291e01a3d20328d157e`.

The helper validates the archive layout and extracts only that executable. It never runs it. The previous qualified baseline was PdfCraft CLI v0.7.1 and FilmCraft Web/WASM v0.7.2 at adapter `1109592c164ebb4b124997ea2d9fd5eff7c5274c`. The current target needs the actual native job's successful result before being described as qualified. These pins are reproducible fixtures, not adapter runtime version restrictions; tool schemas still come from the live CLI. Downloads are opt-in; the adapter never installs a host on a user's behalf.

```bash
python -m pip install -e ".[dev]" -e ./filmcraft-proof pillow pypdf
# Install ffmpeg and poppler-utils through your normal OS package manager.
python scripts/fetch_acceptance_runtime.py
(cd filmcraft-proof && python scripts/fetch-native-runtime.py)
python filmcraft-proof/tests/mcp-e2e.py \
  --mode native --native-cli filmcraft-proof/runtime/filmcraft-cli \
  --output evidence/filmcraft
python scripts/native_storyboard_acceptance.py \
  --media evidence/filmcraft/export.mp4 --output evidence/pdfcraft \
  --pdfcraft-cli runtime/pdfcraft-cli-0.13.0-linux-x86_64/pdfcraft-cli \
  --filmcraft-head b2109ba45833438aadea935401e536dc6f74bc0e
```

The output directory must be new. Results include `filmcraft-pdfcraft-storyboard.pdf`, native and independent PNG renders, extracted frames, source MP4, requested steps, actual MCP results and `mcp-report.json`. GitHub Actions retains the full paired evidence for 14 days. Archive it separately if longer retention is needed.

## Adapter bounds and operational notes

- `PDFCRAFT_ROOT` is mandatory for batches. Use a dedicated directory controlled by the adapter's operator, not a directory simultaneously modified by untrusted local processes. This is an application-level path policy, not an OS sandbox for a hostile native executable.
- Each batch has at most 64 steps and 1 MiB of finite JSON. Inputs are limited to 64 MiB each and outputs to 128 MiB each. New documents are limited to 100 pages/images and 2000-point dimensions; rendering to 144 dpi.
- Only 11 reviewed document operations are accepted. Arguments still come from the installed host's schemas; unsupported or changed schemas fail validation. Remote schema references are rejected.
- Saves and renders use new names. In-place writes and overwrites are rejected. Outputs are staged inside the configured root and published without replacement through held, no-follow directory descriptors. Unsupported platforms fail closed for batches producing files. Linux is covered by the native job; Windows native publication is not implemented.
- Validation failures must preserve every input/output byte. Native failures and timeouts are never retried automatically. Publication may partly succeed if a destination is concurrently created; errors report completed paths so the caller can reconcile rather than replay.
- A batch is one native session. Do not reuse a document ID in a later call. Save the document, then open the path in the next batch.
- Core retains its existing management/introspection facilities. The bounded batch is not a claim that the entire Core server exposes only these 11 operations. Do not expose this local adapter to untrusted remote clients.
- The native acceptance case tests rejected JavaScript, outside-root input, existing-output overwrite and boolean document ID, plus orderly listener shutdown and staging cleanup.

## Original case provenance

The FilmCraft source is a one-second, 12-frame, 160×90 calibration animation with linked 440 Hz audio. Native 50% retiming yields 24 frames at 12 fps and approximately 220 Hz audio. The PDF uses the beginning, middle and final editorial beat from that exported video. It is intentionally a small, repeatable technical case, not a claim of production-resolution video throughput.
