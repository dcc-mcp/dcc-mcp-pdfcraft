"""Original FilmCraft-native frames to a native PdfCraft storyboard case.

This is a native CLI/MCP capability proof, not PdfCraft live-browser coverage.
No PDF authoring library is used: all PDF edits are PdfCraft engine tools.
"""

from __future__ import annotations

import array
import hashlib
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


def inspect_media(media: Path) -> dict:
    """Reject inputs that would make the case's fixed editorial receipt false."""
    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(media)], text=True
        )
    )
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    if (video["width"], video["height"], int(video["nb_read_frames"])) != (160, 90, 24):
        raise ValueError("case expects a 160x90, 24-frame FilmCraft native export")
    if Fraction(video["avg_frame_rate"]) != 12 or abs(float(video["duration"]) - 2) > 0.01:
        raise ValueError("case expects a two-second, 12 fps native export")
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-ss",
            "0.25",
            "-t",
            "1.5",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ]
    )
    samples = array.array("f")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    # Positive zero crossings in a middle window avoid AAC endpoint padding.
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    frequency = crossings * 48000 / len(samples)
    if not 215 <= frequency <= 225:
        raise ValueError("case expects approximately 220 Hz retimed audio")
    return {
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "frames": 24,
        "fps": 12,
        "seconds": float(video["duration"]),
        "audio_hz": frequency,
    }


def make_case(
    media: Path,
    destination: Path,
    filmcraft_head: str,
    host_version: str = "pdfcraft-cli 0.7.1",
    core_version: str = "0.20.44",
) -> dict:
    media_info = inspect_media(media)
    destination.mkdir(parents=True, exist_ok=False)
    copied = destination / "filmcraft-native-export.mp4"
    shutil.copyfile(media, copied)
    frames = []
    for i, index in enumerate((0, 10, 22)):
        frame = destination / f"frame-{i + 1}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(copied),
                "-vf",
                f"select=eq(n\\,{index})",
                "-frames:v",
                "1",
                "-threads",
                "1",
                str(frame),
            ],
            check=True,
        )
        frames.append(frame)
    steps = [
        {
            "tool": "doc_create",
            "args": {
                "from": "blank",
                "pages": 2,
                "width": 960,
                "height": 600,
                "name": "Half-speed storyboard review",
            },
        }
    ]

    def add(tool, **args):
        steps.append({"tool": tool, "args": args})

    def text(page, value, x, y, size=12, width=872, color="#17243b", bold=False, font="helvetica"):
        add(
            "page_add_text",
            doc=1,
            page=page,
            text=value,
            at=[x, y],
            size=size,
            width=width,
            color=color,
            bold=bold,
            font=font,
        )

    def image(page, path, x, y, w, h):
        add("page_add_image", doc=1, page=page, path=str(path.resolve()), rect=[x, y, x + w, y + h])

    for page in (1, 2):
        text(page, "ARTCRAFT   /   CROSS-APP REVIEW", 44, 28, 10, color="#ee5828", bold=True)
        text(
            page,
            f"FILMCRAFT > PDFCRAFT                                         {page:02d} / 02",
            44,
            568,
            10,
            color="#687589",
            font="courier",
        )
    text(1, "Time, held twice.", 44, 66, 50, bold=True)
    text(1, "A half-speed editing study from one native FilmCraft timeline.", 44, 128, 16, color="#56677f")
    text(
        1, "50% PLAYBACK              12 > 24 FRAMES              1.00s > 2.00s", 44, 166, 12, font="courier", bold=True
    )
    titles = ("01 / ESTABLISH", "02 / HOLD", "03 / RELEASE")
    timings = ("00:00.000", "00:00.833", "00:01.833")
    source_frames = (0, 5, 11)
    for i, frame in enumerate(frames):
        x = 44 + i * 296
        text(1, titles[i], x, 208, 12, width=280, bold=True)
        image(1, frame, x, 236, 280, 157.5)
        text(1, timings[i], x, 410, 21, width=280, font="courier", color="#ee5828")
        text(
            1,
            f"Source frame {source_frames[i]:02d} / output frame {i * 10 if i < 2 else 22:02d}",
            x,
            442,
            11,
            width=280,
            color="#56677f",
        )
    text(1, "EDITORIAL CHECK", 44, 488, 11, bold=True)
    text(
        1,
        "The picture and linked audio slow together. Each source frame appears twice.\n"
        "This sheet samples the actual exported video, including its final editorial beat.",
        44,
        510,
        13,
        color="#56677f",
    )
    text(2, "A review sheet with a receipt.", 44, 66, 36, bold=True)
    text(2, "The handoff preserves both the edit and the evidence.", 44, 119, 16, color="#56677f")
    image(2, frames[1], 44, 176, 384, 216)
    text(2, "NATIVE SOFTWARE CHAIN", 474, 170, 11, color="#ee5828", bold=True, width=442)
    text(2, "01  FilmCraft", 474, 203, 21, bold=True, width=442)
    text(
        2,
        "Create, import and retime through Core-backed MCP.\nSave/reopen .fcproj, then export native MP4 + audio.",
        474,
        237,
        12,
        width=442,
        color="#56677f",
    )
    text(2, "02  PdfCraft", 474, 286, 21, bold=True, width=442)
    text(
        2,
        "Create this two-page document in one native session.\n"
        "Place extracted frames and editable page text.\nSave, close, reopen and render the resulting PDF.",
        474,
        320,
        12,
        width=442,
        color="#56677f",
    )
    text(2, "VERIFICATION NOTES", 44, 425, 11, bold=True)
    text(
        2,
        "Video: 24 decoded frames at 12 fps. Audio: approximately 220 Hz after 50% retiming.\n"
        "PDF: native text and image content, two 960 x 600 pt pages, independently rendered.\n"
        "All media is an original procedural calibration source. This is a technical review study.",
        44,
        450,
        12,
        color="#56677f",
    )
    text(
        2,
        "FILMCRAFT " + filmcraft_head[:12] + "   /   " + host_version.upper() + "   /   CORE " + core_version,
        44,
        525,
        9,
        color="#687589",
        font="courier",
    )
    add("doc_set_info", doc=1, key="Title", value="Time, held twice - FilmCraft / PdfCraft storyboard")
    add("doc_set_info", doc=1, key="Subject", value="Core-backed native cross-application storyboard review")
    add("doc_set_info", doc=1, key="Keywords", value="FilmCraft, PdfCraft, storyboard, 50 percent retiming")
    # A reversible real engine edit exercises the native document's undo history.
    add("doc_set_info", doc=1, key="Author", value="ArtCraft workflow acceptance")
    add("edit_undo", doc=1)
    add("edit_redo", doc=1)
    pdf = destination / "filmcraft-pdfcraft-storyboard.pdf"
    add("doc_save", doc=1, path=str(pdf.resolve()))
    add("doc_info", doc=1)
    add("doc_close", doc=1)
    add("doc_open", path=str(pdf.resolve()))
    add("doc_info", doc=2)
    for page in (1, 2):
        steps.append(
            {
                "tool": "page_render",
                "args": {"doc": 2, "page": page, "dpi": 96},
                "out": str((destination / f"native-page-{page}.png").resolve()),
            }
        )
    add("doc_close", doc=2)
    (destination / "steps.json").write_text(json.dumps(steps, indent=2) + "\n")
    return {
        "media_info": media_info,
        "steps": steps,
        "pdf": pdf,
        "source": copied,
        "frames": frames,
        "source_sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
    }


def decode_stream(text):
    decoder = json.JSONDecoder()
    rows = []
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        rows.append(value)
        text = text.lstrip()[end:]
    return rows


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdfcraft-cli", required=True)
    parser.add_argument("--filmcraft-head", default="1109592c164ebb4b124997ea2d9fd5eff7c5274c")
    args = parser.parse_args()
    case = make_case(args.media, args.output, args.filmcraft_head)
    completed = subprocess.run(
        [args.pdfcraft_cli, "run", "--script", str(args.output / "steps.json"), "--root", str(args.output)],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = decode_stream(completed.stdout)
    assert len(rows) == len(case["steps"]), (len(rows), len(case["steps"]))
    (args.output / "native-results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps({"pdf": str(case["pdf"]), "steps": len(rows), "source_sha256": case["source_sha256"]}))
