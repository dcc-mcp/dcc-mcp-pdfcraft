"""Independent decoded-image and PDF-content oracles for the native storyboard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from pypdf import PdfReader


def verify(root: Path) -> dict:
    reader = PdfReader(root / "filmcraft-pdfcraft-storyboard.pdf")
    assert len(reader.pages) == 2
    assert [len(page.images) for page in reader.pages] == [3, 1]
    assert not reader.is_encrypted
    assert reader.metadata.title == "Time, held twice - FilmCraft / PdfCraft storyboard"
    embedded = []
    for page, source_indices in zip(reader.pages, ((1, 2, 3), (2,))):
        for image, index in zip(page.images, source_indices):
            expected = Image.open(root / f"frame-{index}.png").convert("RGB")
            actual = image.image.convert("RGB")
            assert actual.size == expected.size == (160, 90)
            assert actual.tobytes() == expected.tobytes(), "embedded image differs from the FilmCraft-derived frame"
            embedded.append(hashlib.sha256(actual.tobytes()).hexdigest())
    maes = []
    for number in (1, 2):
        native = Image.open(root / f"native-page-{number}.png").convert("RGB")
        independent = Image.open(root / f"independent-{number}.png").convert("RGB")
        assert native.size == independent.size == (1280, 800)
        mae = sum(ImageStat.Stat(ImageChops.difference(native, independent)).mean) / 3
        assert mae < 10, (number, mae)
        maes.append(mae)
    return {
        "passed": True,
        "pages": 2,
        "embedded_images": 4,
        "embedded_frame_pixel_sha256": embedded,
        "native_vs_poppler_mae": maes,
        "pixel_tolerance_255": 10,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = verify(args.root)
    (args.root / "pdf-oracles.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
