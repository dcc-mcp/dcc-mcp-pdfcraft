"""Opt-in PhotoCraft/FilmCraft -> genuine Core MCP -> native PdfCraft proof.

This script never authors a PDF. Two separate native batch invocations create
and save a PDF, then reopen and render it. PDF structure and image planes are
checked independently. No GUI, automatic profile assignment or OCIO is proved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import socket
import struct
import subprocess
import tempfile
import zlib
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_DIMENSION = 2000
MAX_PREVIEW_MAE = 2.0
TITLE = "PhotoCraft and FilmCraft - native PdfCraft asset handoff"
COLOR_CHUNKS = {"sRGB", "gAMA", "iCCP", "cHRM", "cICP"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def inspect_png(data):
    """Admission for this RGB8/RGBA8 untagged case, not all native capabilities."""
    require(0 < len(data) <= MAX_INPUT_BYTES and data[:8] == b"\x89PNG\r\n\x1a\n", "bounded PNG required")
    offset, chunks, header = 8, [], None
    while offset < len(data):
        require(len(chunks) < 256 and len(data) - offset >= 12, "invalid PNG chunk list")
        size = struct.unpack_from(">I", data, offset)[0]
        end = offset + size + 12
        require(end <= len(data), "truncated PNG chunk")
        kind, payload = data[offset + 4 : offset + 8], data[offset + 8 : end - 4]
        require(all(65 <= c <= 90 or 97 <= c <= 122 for c in kind), "invalid PNG chunk name")
        require(zlib.crc32(kind + payload) == struct.unpack_from(">I", data, end - 4)[0], "PNG CRC mismatch")
        name = kind.decode("ascii")
        require(bool(chunks) or name == "IHDR", "PNG must begin with IHDR")
        if name == "IHDR":
            require(header is None and size == 13, "invalid PNG IHDR")
            header = struct.unpack(">IIBBBBB", payload)
        chunks.append(name)
        offset = end
        if name == "IEND":
            require(size == 0 and offset == len(data), "PNG trailing bytes or invalid IEND")
            break
    require(header is not None and chunks[-1:] == ["IEND"] and "IDAT" in chunks, "incomplete PNG")
    width, height, depth, color, compression, filtering, interlace = header
    require(0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION, "PNG dimensions exceed this case")
    require(
        (depth, color, compression, filtering, interlace) in {(8, 2, 0, 0, 0), (8, 6, 0, 0, 0)},
        "case requires noninterlaced RGB8 or RGBA8",
    )
    require(
        not COLOR_CHUNKS.intersection(chunks) and "tRNS" not in chunks,
        "tagged color or color-key transparency requires a separate oracle",
    )
    return {
        "width": width,
        "height": height,
        "mode": "RGB" if color == 2 else "RGBA",
        "bit_depth": 8,
        "chunks": chunks,
        "color_chunks": [],
        "sha256": sha256(data),
        "bytes": len(data),
    }


def _resolved(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _number(value):
    value = _resolved(value)
    require(isinstance(value, (int, float, Decimal)) and not isinstance(value, bool), "PDF number required")
    require(math.isfinite(float(value)), "finite PDF number required")
    return float(value)


def validate_geometry(page, width, height):
    expected = [0.0, 0.0, float(width), float(height)]
    media = _resolved(page.get("/MediaBox"))
    require(isinstance(media, (list, tuple)) and len(media) == 4, "PDF MediaBox required")
    media = [_number(value) for value in media]
    crop = _resolved(page.get("/CropBox", media))
    require(isinstance(crop, (list, tuple)) and len(crop) == 4, "invalid PDF CropBox")
    crop = [_number(value) for value in crop]
    require(all(abs(a - b) <= 1e-6 for a, b in zip(media, expected)), "PDF page geometry differs from source pixels")
    require(all(abs(a - b) <= 1e-6 for a, b in zip(crop, media)), "PDF CropBox changes visible geometry")
    rotation, unit = _number(page.get("/Rotate", 0)), _number(page.get("/UserUnit", 1))
    require(rotation % 360 == 0 and abs(unit - 1.0) <= 1e-9, "PDF rotation or UserUnit changes geometry")
    return {"media_box": media, "effective_crop_box": crop, "rotation": rotation, "user_unit": unit}


def _default_decode(image, channels):
    decode = _resolved(image.get("/Decode"))
    if decode is not None:
        require(isinstance(decode, (list, tuple)) and len(decode) == channels * 2, "invalid PDF image Decode")
        require([_number(x) for x in decode] == [0.0, 1.0] * channels, "non-default PDF image Decode")


def validate_image(image, expected):
    image = _resolved(image)
    require(image.get("/Subtype") == "/Image", "image XObject required")
    require(
        _number(image.get("/Width")) == expected["width"] and _number(image.get("/Height")) == expected["height"],
        "embedded image geometry differs",
    )
    require(
        _number(image.get("/BitsPerComponent")) == 8 and image.get("/ColorSpace") == "/DeviceRGB",
        "untagged case requires DeviceRGB8, not an implicit profile conversion",
    )
    require(not image.get("/ImageMask") and "/Mask" not in image, "unexpected image mask")
    _default_decode(image, 3)
    rgb = image.get_data()
    require(rgb == expected["rgb"], "embedded RGB bytes differ from decoded input")
    alpha = expected.get("alpha")
    needs_mask = alpha is not None and any(value != 255 for value in alpha)
    mask = _resolved(image.get("/SMask"))
    require((mask is not None) == needs_mask, "PDF soft-mask presence differs from source transparency")
    mask_hash = None
    if needs_mask:
        require(
            mask.get("/Subtype") == "/Image"
            and mask.get("/ColorSpace") == "/DeviceGray"
            and _number(mask.get("/BitsPerComponent")) == 8,
            "soft mask must be DeviceGray8",
        )
        require(
            _number(mask.get("/Width")) == expected["width"] and _number(mask.get("/Height")) == expected["height"],
            "soft-mask geometry differs",
        )
        require(
            "/Matte" not in mask and "/Mask" not in mask and not mask.get("/ImageMask"),
            "unexpected soft-mask transform",
        )
        _default_decode(mask, 1)
        require(mask.get_data() == alpha, "embedded alpha plane differs from source")
        mask_hash = sha256(alpha)
    return {
        "color_space": "DeviceRGB",
        "bits_per_component": 8,
        "decoded_rgb_sha256": sha256(rgb),
        "soft_mask": needs_mask,
        "decoded_alpha_sha256": mask_hash,
    }


def compare_preview(actual_rgba, expected_rgba, width, height, max_mae=MAX_PREVIEW_MAE):
    require(len(actual_rgba) == len(expected_rgba) == width * height * 4, "preview geometry/byte length differs")
    require(all(value == 255 for value in actual_rgba[3::4]), "PDF preview must be the opaque white composite")
    expected = bytearray()
    for offset in range(0, len(expected_rgba), 4):
        alpha = expected_rgba[offset + 3]
        expected.extend(
            (value * alpha + 255 * (255 - alpha) + 127) // 255 for value in expected_rgba[offset : offset + 3]
        )
    actual = bytes(value for index, value in enumerate(actual_rgba) if index % 4 != 3)
    differences = [abs(a - b) for a, b in zip(actual, expected)]
    mae = sum(differences) / len(differences)
    require(mae <= max_mae, "rendered image exceeds the predeclared white-composite RGB MAE")
    return {
        "rgb_mae": mae,
        "maximum_channel_error": max(differences),
        "maximum_mae": max_mae,
        "opaque": True,
        "decoded_rgb_sha256": sha256(actual),
    }


def record_check(report, condition, name, detail=None):
    report["checks"].append({"name": name, "ok": bool(condition), "detail": detail})
    if not condition:
        raise AssertionError(name + ": " + str(detail))


def _decode_png(path):
    from PIL import Image

    data = path.read_bytes()
    metadata = inspect_png(data)
    with Image.open(path) as image:
        require(
            image.mode == metadata["mode"] and image.size == (metadata["width"], metadata["height"]),
            "independent PNG decoder differs from container",
        )
        original = image.tobytes()
        rgba = image.convert("RGBA").tobytes()
    rgb = bytes(value for index, value in enumerate(rgba) if index % 4 != 3)
    return {
        **metadata,
        "rgb": rgb,
        "alpha": rgba[3::4] if metadata["mode"] == "RGBA" else None,
        "rgba": rgba,
        "decoded_pixel_sha256": sha256(original),
    }


def prepare_inputs(args, report):
    fixture_root = args.fixtures.resolve()
    provenance_path = fixture_root / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    require(provenance.get("schema_version") == 1, "unsupported fixture provenance schema")
    entries = provenance.get("files", [])
    require(
        len(entries) == 2 and {row.get("role") for row in entries} == {"rgb", "rgba"}, "two PhotoCraft roles required"
    )
    input_dir = args.output / "inputs"
    input_dir.mkdir()
    inputs, originals = [], {provenance_path: sha256(provenance_path.read_bytes())}
    for role in ("rgb", "rgba"):
        entry = next(row for row in entries if row["role"] == role)
        name = entry.get("name")
        require(
            isinstance(name, str) and Path(name).name == name and name not in {"", ".", ".."},
            "fixture basename required",
        )
        source = fixture_root / name
        require(source.is_file() and not source.is_symlink(), "regular fixture file required")
        data = source.read_bytes()
        require(sha256(data) == entry.get("sha256"), "PhotoCraft fixture hash differs from producer receipt")
        target = input_dir / ("photocraft-" + role + ".png")
        target.write_bytes(data)
        decoded = _decode_png(target)
        require(decoded["mode"] == role.upper(), "PhotoCraft fixture role differs from decoded mode")
        require(
            decoded["decoded_pixel_sha256"] == entry.get("decoded_pixel_sha256"), "PhotoCraft pixel receipt differs"
        )
        inputs.append({"role": "photocraft-" + role, "path": target, **decoded})
        originals[source] = sha256(data)
    media = args.media.resolve()
    require(media.is_file() and media.stat().st_size <= 32 * 1024 * 1024, "bounded existing FilmCraft export required")
    originals[media] = sha256(media.read_bytes())
    if args.expected_media_sha256:
        require(originals[media] == args.expected_media_sha256, "FilmCraft export differs from expected hash")
    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(media)],
            text=True,
            timeout=60,
        )
    )
    video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video")
    width, height, frames = int(video["width"]), int(video["height"]), int(video["nb_read_frames"])
    require(
        video["codec_name"] == "h264" and 0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION,
        "bounded H.264 geometry required",
    )
    require(0 < frames <= 1800 and 0 < Fraction(video["avg_frame_rate"]) <= 60, "bounded FilmCraft timing required")
    middle = frames // 2
    pixels = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(media),
            "-vf",
            f"select=eq(n\\,{middle})",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=60,
    )
    require(len(pixels) == width * height * 3, "independent FilmCraft frame decode size differs")
    from PIL import Image

    frame = input_dir / "filmcraft-decoded-frame.png"
    Image.frombytes("RGB", (width, height), pixels).save(frame)
    inputs.append({"role": "filmcraft-frame", "path": frame, **_decode_png(frame)})
    report["photo_producer"] = provenance.get("producer", {})
    report["photo_fixture_license"] = provenance.get("license")
    report["photo_fixture_source"] = provenance.get("source")
    report["filmcraft_source"] = {
        "sha256": originals[media],
        "adapter_head": args.filmcraft_head,
        "frame_index": middle,
        "codec": video["codec_name"],
        "frame_count": frames,
        "color": {
            key: video.get(key)
            for key in ("pix_fmt", "color_range", "color_space", "color_transfer", "color_primaries")
        },
        "decode": "FFmpeg RGB24 numeric decode; saved as untagged PNG; no profile assignment proved",
        "license": "not inferred from the container; the supplying workflow owns source-media permission",
    }
    report["inputs"] = [
        {key: value for key, value in row.items() if key not in {"rgb", "alpha", "rgba", "path"}} for row in inputs
    ]
    record_check(report, True, "producer fixture hashes and decoded pixels verified before native execution")
    return inputs, originals


def run_core_batches(args, inputs, report):
    from dcc_mcp_core import __version__ as core_version
    from native_storyboard_acceptance import McpClient

    from dcc_mcp_pdfcraft.server import PdfCraftMcpServer

    pdf = args.output / "photocraft-filmcraft-handoff.pdf"
    create = [
        {"tool": "doc_create", "args": {"from": "images", "paths": [str(row["path"]) for row in inputs], "dpi": 72}},
        {"tool": "doc_set_info", "args": {"doc": 1, "key": "Title", "value": TITLE}},
        {"tool": "doc_info", "args": {"doc": 1}},
        {"tool": "doc_save", "args": {"doc": 1, "path": str(pdf)}},
        {"tool": "doc_close", "args": {"doc": 1}},
    ]
    reopen = [{"tool": "doc_open", "args": {"path": str(pdf)}}, {"tool": "doc_info", "args": {"doc": 1}}]
    reopen.extend(
        {
            "tool": "page_render",
            "args": {"doc": 1, "page": index, "dpi": 72},
            "out": str(args.output / f"native-page-{index}.png"),
        }
        for index in range(1, len(inputs) + 1)
    )
    reopen.append({"tool": "doc_close", "args": {"doc": 1}})
    report["steps_requested"] = {"create_save_close": create, "fresh_open_render_close": reopen}
    report["core_version"] = core_version
    report["native_version"] = subprocess.check_output([args.pdfcraft_cli, "--version"], text=True, timeout=20).strip()
    with tempfile.TemporaryDirectory(prefix="asset-handoff-registry-", dir=args.output) as registry:
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
            initialized = client.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "native-asset-handoff-agent", "version": "1"},
                },
            )
            client.request("notifications/initialized", {}, notification=True)
            record_check(
                report, initialized["serverInfo"]["name"] == "dcc-mcp-pdfcraft", "genuine Core MCP initialized"
            )
            report["mcp_protocol"] = initialized["protocolVersion"]
            client.call("load_skill", {"skill_name": "pdfcraft-core"})
            names = [row["name"] for row in client.tools()]

            def tool(suffix):
                matches = [name for name in names if name == suffix or name.endswith("__" + suffix)]
                require(len(matches) == 1, "exact packaged tool not discovered: " + suffix)
                return matches[0]

            catalog = client.call(tool("list_pdfcraft_tools"), {})
            report["live_catalog_sha256"] = sha256(json.dumps(catalog, sort_keys=True).encode())
            batch = tool("execute_pdfcraft_batch")
            for label, steps, outputs in (
                ("create_save_close", create, 1),
                ("fresh_open_render_close", reopen, len(inputs)),
            ):
                result = client.call(batch, {"steps": steps})
                report.setdefault("batch_responses", {})[label] = result
                payload = result["result"]
                record_check(report, len(payload["steps"]) == len(steps), label + " native results complete")
                record_check(report, len(payload["outputs"]) == outputs, label + " output count correct")
                document = payload["steps"][0]
                record_check(
                    report,
                    document.get("doc") == 1 and document.get("pages") == len(inputs),
                    label + " fresh batch document id and page count read back",
                )
                for artifact in payload["outputs"]:
                    path = Path(artifact["path"])
                    record_check(
                        report,
                        path.parent == args.output
                        and path.is_file()
                        and not path.is_symlink()
                        and path.stat().st_size == artifact["bytes"]
                        and sha256(path.read_bytes()) == artifact["sha256"],
                        label + " published output matches native receipt: " + path.name,
                    )
                if label == "create_save_close":
                    report["saved_pdf_sha256"] = sha256(pdf.read_bytes())
            report["mcp_requests_including_notifications"] = client.counter
            report["native_batch_calls"] = 2
            report["native_steps_returned"] = len(create) + len(reopen)
            record_check(
                report,
                sha256(pdf.read_bytes()) == report["saved_pdf_sha256"],
                "fresh-process reopen and renders leave saved PDF bytes unchanged",
            )
            record_check(report, pdf.is_file(), "native PDF saved and reopened in a new batch process")
            record_check(report, not list(args.output.glob(".pdfcraft-batch-*")), "native staging cleaned")
        finally:
            server.stop()
            if port:
                with socket.socket() as connection:
                    record_check(report, connection.connect_ex(("127.0.0.1", port)) != 0, "owned MCP listener closed")
    return pdf


def verify_outputs(pdf, inputs, output, report):
    from PIL import Image
    from pypdf import PdfReader

    require(pdf.read_bytes().splitlines()[0] == b"%PDF-1.7", "fixture PDF header is not 1.7")
    reader = PdfReader(pdf, strict=True)
    require(not reader.is_encrypted and len(reader.pages) == len(inputs), "PDF page count/encryption differs")
    require(reader.metadata.title == TITLE, "PDF title did not survive save/reopen")
    catalog = _resolved(reader.trailer["/Root"])
    require(catalog.get("/Version", "/1.7") == "/1.7", "PDF catalog overrides the 1.7 version contract")
    require(not any(key in catalog for key in ("/OpenAction", "/AA", "/AcroForm")), "unexpected active PDF content")
    names = _resolved(catalog.get("/Names", {}))
    require(not any(key in names for key in ("/JavaScript", "/EmbeddedFiles")), "unexpected PDF script/attachment")
    require("/OutputIntents" not in catalog, "untagged fixture unexpectedly acquired an output profile")
    pages = []
    for index, (page, expected) in enumerate(zip(reader.pages, inputs), 1):
        require(not page.get("/Annots") and "/AA" not in page, "unexpected page annotation/action")
        geometry = validate_geometry(page, expected["width"], expected["height"])
        resources = _resolved(page["/Resources"])
        objects = _resolved(resources.get("/XObject", {}))
        require(len(objects) == 1, "one source image XObject per page required")
        image = validate_image(next(iter(objects.values())), expected)
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(index),
                "-l",
                str(index),
                "-singlefile",
                "-r",
                "72",
                "-png",
                str(pdf),
                str(output / f"independent-page-{index}"),
            ],
            check=True,
            timeout=60,
        )
        previews = {}
        for kind in ("native", "independent"):
            with Image.open(output / f"{kind}-page-{index}.png") as rendered:
                require(rendered.size == (expected["width"], expected["height"]), "render dimensions differ")
                require(rendered.mode in {"RGB", "RGBA"}, "RGB/RGBA preview required")
                previews[kind] = {
                    "png_mode": rendered.mode,
                    **compare_preview(
                        rendered.convert("RGBA").tobytes(), expected["rgba"], expected["width"], expected["height"]
                    ),
                }
        pages.append({"role": expected["role"], **geometry, "image": image, "previews": previews})
        record_check(report, True, f"page {index} geometry, raw RGB/alpha and both white-composite renderers")
    report["pdf_contract"] = {
        "header": "PDF-1.7",
        "pages": pages,
        "render_dpi": 72,
        "untagged_device_rgb": True,
        "automatic_srgb_assignment_verified": False,
        "ocio_verified": False,
        "scene_linear_verified": False,
        "preview_alpha_preserved": False,
        "gui_verified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--expected-media-sha256")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdfcraft-cli", required=True)
    parser.add_argument("--expected-cli-sha256", required=True)
    parser.add_argument("--filmcraft-head")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests/fixtures/photocraft-qualified")
    args = parser.parse_args(argv)
    require(re.fullmatch(r"[0-9a-f]{64}", args.expected_cli_sha256) is not None, "expected CLI SHA-256 required")
    require(
        args.filmcraft_head is None or re.fullmatch(r"[0-9a-f]{40}", args.filmcraft_head),
        "full FilmCraft source SHA required",
    )
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "passed": False,
        "checks": [],
        "transport": "Core MCP HTTP -> owned native CLI batches",
        "oracle_declared_before_execution": {
            "embedded_rgb_and_alpha": "exact decoded planes",
            "render_dpi": 72,
            "preview": "opaque white composite",
            "maximum_rgb_mae": MAX_PREVIEW_MAE,
        },
        "producer_checks_reused_not_rerun": True,
        "full_consumer_contract_completed": False,
    }
    inputs, originals = [], {}
    try:
        binary = Path(args.pdfcraft_cli).resolve()
        require(
            binary.is_file() and sha256(binary.read_bytes()) == args.expected_cli_sha256,
            "configured official CLI hash differs from trusted provenance",
        )
        args.pdfcraft_cli = str(binary)
        report["native_cli_sha256"] = args.expected_cli_sha256
        inputs, originals = prepare_inputs(args, report)
        pdf = run_core_batches(args, inputs, report)
        verify_outputs(pdf, inputs, args.output, report)
        record_check(
            report,
            all(sha256(path.read_bytes()) == digest for path, digest in originals.items()),
            "all original producer inputs and manifest remain unchanged",
        )
        record_check(
            report,
            all(sha256(row["path"].read_bytes()) == row["sha256"] for row in inputs),
            "all staged input PNG bytes remain unchanged",
        )
        report["passed"] = True
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)[:1500]}
        raise
    finally:
        report["artifacts"] = [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
            for path in sorted(args.output.iterdir())
            if path.is_file() and not path.is_symlink() and path.suffix in {".pdf", ".png"}
        ]
        report["input_integrity_after"] = {
            "originals_checked": len(originals),
            "originals_unchanged": all(
                path.is_file() and sha256(path.read_bytes()) == digest for path, digest in originals.items()
            ),
            "staged_checked": len(inputs),
            "staged_unchanged": all(
                row["path"].is_file() and sha256(row["path"].read_bytes()) == row["sha256"] for row in inputs
            ),
        }
        (args.output / "asset-handoff-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
