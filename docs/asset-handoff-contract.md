# PhotoCraft and FilmCraft assets in native PdfCraft

This opt-in acceptance extends the existing FilmCraft storyboard proof. It does
not rerun PhotoCraft's producer workflow or the Nuke consumer tests. It consumes
two immutable, original PhotoCraft test images and one independently decoded
frame from the current FilmCraft native export.

The test uses the public `pdfcraft-core` Skill and genuine Core MCP HTTP.
`execute_pdfcraft_batch` starts one native CLI process per call. The first call
creates a three-page PDF, sets its title, inspects, saves and closes it. The
second call opens that saved PDF in a new process, inspects, renders all pages
and closes it. Document id 1 is local to each fresh batch; no id is transferred
between native processes. All arguments still pass the installed host's live
schema validation. No new generic host tool or copied command catalog is added.

## Inputs and provenance

`tests/fixtures/photocraft-qualified/provenance.json` identifies the original
PhotoCraft RGB/RGBA outputs, producer commit, runtime and CI receipt. File hashes
and independently decoded pixel hashes must match before native execution.
These procedural graphics contain no third-party images; the fixture's license
declaration is separate from vendor binaries, fonts and other assets.

The supplied FilmCraft MP4 is limited to a small H.264 sequence. FFprobe records
its actual dimensions, frame count, rate, pixel format and color metadata.
FFmpeg decodes its middle frame to numeric RGB24 pixels; these pixels are saved
as an untagged PNG. The test records the MP4 and decoded-image hashes. It does
not assert that a lossy H.264 frame equals PhotoCraft's original image.
`--expected-media-sha256` can additionally bind an externally supplied export.

The configured PdfCraft CLI must match `--expected-cli-sha256` before it runs.
That hash is supplied from the separately verified official runtime fixture.
The script records the actual host version; it does not restrict adapter
compatibility to an exact version string.

## Machine-checkable contract

Only noninterlaced, untagged RGB8 and RGBA8 PNG are admitted by this case.
ICC/gAMA/sRGB/cHRM/cICP tags, color-key transparency, palettes and 16-bit data
need separate oracles. This case's dimensions are at most 2000 pixels per side.
Explicit `doc_create` resolution of 72 dpi overrides any PNG physical resolution:
one source pixel becomes one PDF point and a 72 dpi render has the original
integer dimensions.

| Layer | Required readback |
|---|---|
| PDF container | Actual `%PDF-1.7` header, three unencrypted pages, retained title; no script, launch action, form or attachment in the fixture |
| Page geometry | `MediaBox=[0,0,width,height]`; effective CropBox identical; Rotate=0 and UserUnit=1 defaults or equivalent explicit values |
| RGB image | One image XObject per page, original dimensions, DeviceRGB and 8 bits/component; decompressed RGB bytes exactly match the decoded source |
| Alpha | A nonopaque RGBA input has an 8-bit DeviceGray SMask with exact original alpha bytes; opaque inputs have no SMask; no color-key Mask, altered Decode or Matte is accepted; image and SMask Interpolate must be false or absent |
| Native preview | Original pixel dimensions, RGB/RGBA PNG with fully opaque alpha; compare against the independently computed white composite |
| Independent preview | Poppler renders at fixed 288 dpi; the center `(4*x+2,4*y+2)` of every 4×4 cell is sampled without filtering to the original pixel grid, then checked with the same white-composite oracle |
| Integrity | Original fixtures, provenance, MP4 and staged PNG hashes remain unchanged; all output hashes are recorded |

The RGB mean absolute channel-error ceiling for each rendered preview is
**2/255**, declared before execution. Maximum observed channel error and decoded
pixel hashes are retained. A failed run is not converted to success by widening
the threshold. Raw RGB/SMask equality and opaque rendered appearance are separate
checks: a white preview does not itself prove alpha-plane retention.

### Independent renderer sampling limitation

The first actual Linux case exposed an under-specified independent raster
reference. Its PDF RGB and SMask planes were exact, and the native 72 dpi RGBA
page matched the white-composite oracle with MAE 0. Poppler's default 72 dpi
render had MAE 2.957465 and maximum channel error 208. All 333 differing pixels
were on image/alpha boundaries; all 5484 interior pixels were exact. That failed
report and the original renders remain failed evidence, not a passing run.
The first CI report did not record Poppler's version; new receipts record it.

Poppler's Splash renderer can interpolate image and mask samples at 1× even
when their PDF Interpolate flag is false. Its coordinate bounds include a
`floor(upper)+1` sample, so a 96×64 page can use a 97×65 intermediate image.
Font/vector antialias switches do not disable that image interpolation. The
[26.07.0 Splash source](https://github.com/tsdgeos/poppler_mirror/blob/b7989c187daee307b782ac4592cddb1ae0677b76/splash/Splash.cc#L3967)
documents the interpolation selection. This is the version-matched source
commit, not an attestation of a binary build or the original CI installation.

The corrected reference makes the sampling contract explicit: fixed 4× raster,
Interpolate=false, then one fixed center sample per original pixel. No RGB value
is fitted, no edge is excluded, and the MAE ceiling remains 2. The raw high
resolution PNG, normalized PNG, dimensions, center offset, decoded pixel hashes,
Poppler version and executable hash are retained. A data-only diagnostic using
the already provided Poppler 26.07.0 reproduced the 72 dpi discrepancy and
yielded MAE 0/max 0 for every pixel with the fixed 4× method. This diagnostic is
not a Windows PdfCraft output/publication test; a new real Linux run is still
required after the contract correction.

This qualifies the specified pixel-grid comparison, not identical pixels in
Poppler's default 72 dpi display mode or all PDF viewers. Native rendering stays
at 72 dpi; the higher independent raster does not change the adapter's 144 dpi
native publication ceiling or expose a new agent operation.

The image-only PDF path in official PdfCraft v0.13 source uses DeviceRGB8 for
untagged RGB/RGBA PNG, a separate DeviceGray8 soft mask for nonopaque alpha and
PDF 1.7 creation. The native page renderer clears to white. These source facts
motivate the oracle; only an actual acceptance run qualifies the combination.

DeviceRGB is not an automatic sRGB assignment. The script does not establish
ICC conversion accuracy, OCIO, scene-linear values, PDF/A or PDF/X conformance,
preview alpha preservation, desktop GUI control, or the full 19-PNG consumer
contract. PhotoCraft `.pcraft` and FilmCraft `.fcproj` are not imported here.

## Run and evidence

```sh
python scripts/native_asset_handoff_acceptance.py \
  --media evidence/filmcraft/export.mp4 \
  --output evidence/asset-handoff \
  --pdfcraft-cli runtime/pdfcraft-cli-0.13.0-linux-x86_64/pdfcraft-cli \
  --expected-cli-sha256 2283260fc06a2cadc32c2836fcdd29ed2996418ab3c03291e01a3d20328d157e \
  --filmcraft-head b2109ba45833438aadea935401e536dc6f74bc0e
```

Pillow and pypdf are independent test oracles, while FFmpeg/FFprobe and Poppler
must already be available. The output directory must be new. Results include
the native PDF, three native previews, three independent previews, staged inputs
and `asset-handoff-report.json`. A partial report remains false on error, and
an uncertain native batch is never automatically replayed.

Native PDF/PNG publication still requires the existing POSIX descriptor-relative,
no-follow support. The test does not substitute direct CLI writes on Windows or
weaken that gate. Runtime fetching and Linux CI qualification remain separate
from the offline helper tests. No browser or DCC-CUA path is used by this case.
